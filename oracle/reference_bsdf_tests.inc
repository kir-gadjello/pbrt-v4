// Reference-oracle qualification tests.
//
// This file is appended to src/pbrt/bsdfs_test.cpp by the reference-oracle CI
// workflow so it can exercise PBRT internals without adding another test binary.
// Keep expectations analytic: no golden image is treated as truth here.

namespace {

double OracleFresnelDielectric(double cosThetaI, double eta) {
    double etaI = 1.0, etaT = eta;
    if (cosThetaI < 0) {
        std::swap(etaI, etaT);
        cosThetaI = -cosThetaI;
    }
    cosThetaI = std::min(1.0, std::max(0.0, cosThetaI));
    double sinThetaI = std::sqrt(std::max(0.0, 1.0 - cosThetaI * cosThetaI));
    double sinThetaT = etaI / etaT * sinThetaI;
    if (sinThetaT >= 1.0)
        return 1.0;
    double cosThetaT = std::sqrt(std::max(0.0, 1.0 - sinThetaT * sinThetaT));
    double rs = (etaI * cosThetaI - etaT * cosThetaT) /
                (etaI * cosThetaI + etaT * cosThetaT);
    double rp = (etaT * cosThetaI - etaI * cosThetaT) /
                (etaT * cosThetaI + etaI * cosThetaT);
    return 0.5 * (rs * rs + rp * rp);
}

}  // namespace

TEST(ReferenceOracle, FresnelDielectricMatchesClosedForm) {
    for (double eta : {1.1, 1.25, 1.333, 1.5, 1.7, 2.0}) {
        for (double cosTheta : {1.0, 0.95, 0.8, 0.5, 0.25, 0.1,
                                -0.95, -0.8, -0.7, -0.5, -0.25}) {
            double expected = OracleFresnelDielectric(cosTheta, eta);
            double got = FrDielectric((Float)cosTheta, (Float)eta);
            EXPECT_NEAR(got, expected, 2e-6)
                << "eta=" << eta << " cosTheta=" << cosTheta;
        }
    }
}

TEST(ReferenceOracle, FresnelNormalIncidenceAndCriticalAngle) {
    for (double eta : {1.1, 1.25, 1.333, 1.5, 2.0}) {
        double r0 = std::pow((eta - 1.0) / (eta + 1.0), 2.0);
        EXPECT_NEAR(FrDielectric(1, (Float)eta), r0, 2e-7) << "eta=" << eta;

        // Negative cosine denotes incidence from the eta side into air.
        double critical = std::asin(1.0 / eta);
        double thetaAbove = std::min(1.560, critical + 0.03);
        double cosAbove = std::cos(thetaAbove);
        EXPECT_EQ(FrDielectric((Float)-cosAbove, (Float)eta), 1)
            << "eta=" << eta << " critical=" << critical;
    }
}

TEST(ReferenceOracle, SmoothDielectricSamplingUsesFresnelProbability) {
    constexpr int n = 100000;
    for (Float eta : {1.25f, 1.333f, 1.5f, 2.f}) {
        TrowbridgeReitzDistribution smooth(0, 0);
        DielectricBxDF bxdf(eta, smooth);
        for (Float cosTheta : {1.f, .8f, .5f, .25f}) {
            Vector3f wo(SafeSqrt(1 - Sqr(cosTheta)), 0, cosTheta);
            Float expectedR = FrDielectric(cosTheta, eta);
            int reflected = 0;
            for (int i = 0; i < n; ++i) {
                Float uc = (i + Float(.5)) / n;
                auto bs = bxdf.Sample_f(wo, uc, Point2f(.37f, .73f),
                                        TransportMode::Importance,
                                        BxDFReflTransFlags::All);
                ASSERT_TRUE(bs.has_value());
                reflected += bs->IsReflection();
            }
            Float measured = Float(reflected) / n;
            EXPECT_NEAR(measured, expectedR, 1.1f / n)
                << "eta=" << eta << " cosTheta=" << cosTheta;
        }
    }
}

TEST(ReferenceOracle, SmoothDielectricImportanceWhiteFurnaceIsUnitThroughput) {
    // Under importance transport a lossless, smooth dielectric sample has
    // f*|cos(theta_i)|/pdf == 1 for both Fresnel branches. This catches
    // branch-probability/throughput mismatches independently of image rendering.
    for (Float eta : {1.1f, 1.25f, 1.333f, 1.5f, 2.f}) {
        TrowbridgeReitzDistribution smooth(0, 0);
        DielectricBxDF bxdf(eta, smooth);
        for (Float cosTheta : {1.f, .9f, .7f, .5f, .25f}) {
            Vector3f wo(SafeSqrt(1 - Sqr(cosTheta)), 0, cosTheta);
            for (Float uc : {.001f, .02f, .2f, .5f, .8f, .98f, .999f}) {
                auto bs = bxdf.Sample_f(wo, uc, Point2f(.19f, .61f),
                                        TransportMode::Importance,
                                        BxDFReflTransFlags::All);
                ASSERT_TRUE(bs.has_value());
                ASSERT_GT(bs->pdf, 0);
                SampledSpectrum w = bs->f * AbsCosTheta(bs->wi) / bs->pdf;
                for (int c = 0; c < NSpectrumSamples; ++c)
                    EXPECT_NEAR(w[c], 1.f, 3e-6f)
                        << "eta=" << eta << " cosTheta=" << cosTheta
                        << " uc=" << uc << " channel=" << c;
            }
        }
    }
}

TEST(ReferenceOracle, InternalTotalReflectionSamplesReflectionOnly) {
    constexpr Float eta = 1.5f;
    TrowbridgeReitzDistribution smooth(0, 0);
    DielectricBxDF bxdf(eta, smooth);

    // From glass to air at 50 degrees: theta_c = asin(1/1.5) ~= 41.81 deg.
    Float theta = Radians(50.f);
    Vector3f wo(std::sin(theta), 0, -std::cos(theta));
    ASSERT_EQ(FrDielectric(CosTheta(wo), eta), 1);
    for (int i = 0; i < 64; ++i) {
        auto bs = bxdf.Sample_f(wo, (i + .5f) / 64.f, Point2f(.5f, .5f),
                                TransportMode::Importance,
                                BxDFReflTransFlags::All);
        ASSERT_TRUE(bs.has_value());
        EXPECT_TRUE(bs->IsReflection());
        EXPECT_FALSE(bs->IsTransmission());
        EXPECT_EQ(bs->pdf, 1);
    }
}

TEST(ReferenceOracle, DiffuseSampleEstimatorMatchesAlbedoExactly) {
    for (Float rho : {.02f, .18f, .5f, .9f, 1.f}) {
        DiffuseBxDF bxdf{SampledSpectrum(rho)};
        Vector3f wo(0, 0, 1);
        for (Point2f u : {Point2f(.01f, .03f), Point2f(.2f, .8f),
                          Point2f(.51f, .49f), Point2f(.99f, .7f)}) {
            auto bs = bxdf.Sample_f(wo, .5f, u, TransportMode::Radiance,
                                    BxDFReflTransFlags::All);
            ASSERT_TRUE(bs.has_value());
            ASSERT_GT(bs->pdf, 0);
            SampledSpectrum estimate = bs->f * AbsCosTheta(bs->wi) / bs->pdf;
            for (int c = 0; c < NSpectrumSamples; ++c)
                EXPECT_NEAR(estimate[c], rho, 2e-6f);
        }
    }
}
