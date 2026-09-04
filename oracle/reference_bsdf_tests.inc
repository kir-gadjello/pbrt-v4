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

// Fast-math/excess-precision optimization can inline x - floor(x) so that the
// subtraction sees an unrounded multiply while floor() sees the rounded Float.
// Around integer boundaries that produced a small negative number (for example
// -1.62e-4) and fed an invalid sample to the VNDF. Wrap once more so this test
// helper honors the same [0,1) contract as a real PBRT sampler.
Float OracleFract(Float x) {
    Float f = x - std::floor(x);
    if (f < 0)
        f += 1;
    if (f >= 1)
        f -= 1;
    return f;
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

TEST(ReferenceOracle, SmoothDielectricRadianceTransmissionHasEtaJacobian) {
    // In radiance transport PBRT uses the adjoint eta^2 factor for transmission.
    // Reflection remains unit sample throughput. Test both entering and exiting
    // interfaces to prevent a silent eta-orientation inversion.
    for (Float eta : {1.1f, 1.333f, 1.5f, 2.f}) {
        TrowbridgeReitzDistribution smooth(0, 0);
        DielectricBxDF bxdf(eta, smooth);
        for (Float cosTheta : {.95f, .7f, .4f, -.95f, -.8f}) {
            Vector3f wo(SafeSqrt(1 - Sqr(cosTheta)), 0, cosTheta);
            Float R = FrDielectric(cosTheta, eta);
            if (R == 1)
                continue;
            // Force transmission by choosing uc strictly in the transmission range.
            Float uc = R + (1 - R) * .5f;
            auto bs = bxdf.Sample_f(wo, uc, Point2f(.31f, .67f),
                                    TransportMode::Radiance,
                                    BxDFReflTransFlags::All);
            ASSERT_TRUE(bs.has_value());
            ASSERT_TRUE(bs->IsTransmission());
            ASSERT_GT(bs->pdf, 0);
            SampledSpectrum w = bs->f * AbsCosTheta(bs->wi) / bs->pdf;
            Float expected = 1 / Sqr(bs->eta);
            for (int c = 0; c < NSpectrumSamples; ++c)
                EXPECT_NEAR(w[c], expected, 4e-6f)
                    << "eta=" << eta << " cosTheta=" << cosTheta
                    << " sample_eta=" << bs->eta;
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

TEST(ReferenceOracle, RoughDielectricSampleMatchesEvaluationAndPdf) {
    // Target the rough branch implicated by dielectric-reflectivity regressions.
    // A sampled direction must evaluate to the same f and density as the general
    // f()/PDF() path, on either side of the interface.
    for (Float eta : {1.1f, 1.333f, 1.5f, 2.f}) {
        for (Float alpha : {.03f, .08f, .2f, .5f}) {
            TrowbridgeReitzDistribution distrib(alpha, alpha * 1.37f);
            DielectricBxDF bxdf(eta, distrib);
            for (Float cosTheta : {.97f, .72f, .37f, -.93f, -.65f}) {
                Vector3f wo(SafeSqrt(1 - Sqr(cosTheta)), 0, cosTheta);
                int valid = 0;
                for (int i = 1; i <= 4096; ++i) {
                    Float uc = OracleFract(Float(i) * .7548776662466927f);
                    Point2f u(OracleFract(Float(i) * .5698402909980532f),
                              OracleFract(Float(i) * .4385790218125324f));
                    auto bs = bxdf.Sample_f(wo, uc, u, TransportMode::Radiance,
                                            BxDFReflTransFlags::All);
                    if (!bs)
                        continue;
                    ++valid;
                    ASSERT_GT(bs->pdf, 0);
                    Float pdf = bxdf.PDF(wo, bs->wi, TransportMode::Radiance,
                                         BxDFReflTransFlags::All);
                    Float pdfTol = std::max(Float(2e-6), Float(4e-4) * std::abs(pdf));
                    EXPECT_NEAR(bs->pdf, pdf, pdfTol)
                        << "eta=" << eta << " alpha=" << alpha
                        << " cosTheta=" << cosTheta << " i=" << i;
                    SampledSpectrum f = bxdf.f(wo, bs->wi, TransportMode::Radiance);
                    for (int c = 0; c < NSpectrumSamples; ++c) {
                        Float fTol = std::max(Float(2e-6), Float(5e-4) * std::abs(f[c]));
                        EXPECT_NEAR(bs->f[c], f[c], fTol)
                            << "eta=" << eta << " alpha=" << alpha
                            << " cosTheta=" << cosTheta << " i=" << i
                            << " channel=" << c;
                    }
                }
                EXPECT_GT(valid, 3000);
            }
        }
    }
}

TEST(ReferenceOracle, RoughDielectricReflectionReciprocity) {
    // Helmholtz reciprocity for reflection is unambiguous (no eta^2 transport
    // convention is involved), making it a strong independent algebraic check.
    for (Float eta : {1.1f, 1.333f, 1.5f, 2.f}) {
        for (Float alpha : {.04f, .15f, .45f}) {
            TrowbridgeReitzDistribution distrib(alpha, alpha * .73f);
            DielectricBxDF bxdf(eta, distrib);
            for (int i = 1; i <= 512; ++i) {
                Float z0 = .08f + .91f * OracleFract(Float(i) * .61803398875f);
                Float z1 = .08f + .91f * OracleFract(Float(i) * .41421356237f);
                Float p0 = 2 * Pi * OracleFract(Float(i) * .73205080757f);
                Float p1 = 2 * Pi * OracleFract(Float(i) * .23606797750f);
                Vector3f wo(SafeSqrt(1 - Sqr(z0)) * std::cos(p0),
                            SafeSqrt(1 - Sqr(z0)) * std::sin(p0), z0);
                Vector3f wi(SafeSqrt(1 - Sqr(z1)) * std::cos(p1),
                            SafeSqrt(1 - Sqr(z1)) * std::sin(p1), z1);
                SampledSpectrum a = bxdf.f(wo, wi, TransportMode::Radiance);
                SampledSpectrum b = bxdf.f(wi, wo, TransportMode::Radiance);
                for (int c = 0; c < NSpectrumSamples; ++c) {
                    Float tol = std::max(Float(3e-6), Float(3e-5) * std::max(a[c], b[c]));
                    EXPECT_NEAR(a[c], b[c], tol)
                        << "eta=" << eta << " alpha=" << alpha << " i=" << i;
                }
            }
        }
    }
}

TEST(ReferenceOracle, RoughDielectricImportanceWhiteFurnaceDoesNotCreateEnergy) {
    // A single-scattering GGX dielectric may lose energy through masked multiple
    // scattering, but a passive lossless interface must never create it.
    for (Float eta : {1.1f, 1.333f, 1.5f, 2.f}) {
        for (Float alpha : {.04f, .15f, .4f, .8f}) {
            TrowbridgeReitzDistribution distrib(alpha, alpha);
            DielectricBxDF bxdf(eta, distrib);
            for (Float cosTheta : {.95f, .65f, .3f}) {
                Vector3f wo(SafeSqrt(1 - Sqr(cosTheta)), 0, cosTheta);
                double sum = 0;
                constexpr int n = 65536;
                for (int i = 1; i <= n; ++i) {
                    Float uc = OracleFract(Float(i) * .7548776662466927f);
                    Point2f u(OracleFract(Float(i) * .5698402909980532f),
                              OracleFract(Float(i) * .4385790218125324f));
                    auto bs = bxdf.Sample_f(wo, uc, u, TransportMode::Importance,
                                            BxDFReflTransFlags::All);
                    if (!bs)
                        continue;
                    sum += double(bs->f[0] * AbsCosTheta(bs->wi) / bs->pdf);
                }
                double energy = sum / n;
                EXPECT_LE(energy, 1.004)
                    << "eta=" << eta << " alpha=" << alpha
                    << " cosTheta=" << cosTheta;
                EXPECT_GT(energy, .15)
                    << "pathological energy loss eta=" << eta << " alpha=" << alpha
                    << " cosTheta=" << cosTheta;
            }
        }
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
