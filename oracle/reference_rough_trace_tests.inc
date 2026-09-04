// Diagnostic/reference contract for the rough dielectric sampling algebra.
// This deliberately replays the production formulas using PBRT's own Float,
// VNDF sampler, Fresnel, refraction, and GGX helpers. It must remain finite for
// all deterministic white-furnace stress samples. Keeping it separate from
// DielectricBxDF::Sample_f() makes failures observable instead of CHECK-aborting.

namespace {
PBRT_CPU_GPU Float ReferenceRoughTraceFract(Float x) {
    // Keep deterministic quasi-random stress samples within the sampler's
    // documented [0,1) domain even when fast-math/excess precision evaluates
    // x - floor(x) on opposite sides of a rounded integer boundary.
    Float f = x - std::floor(x);
    if (f < 0)
        f += 1;
    if (f >= 1)
        f -= 1;
    return f;
}
}

TEST(ReferenceRoughTrace, TransmissionPdfAlgebraIsFinite) {
    for (Float eta : {1.1f, 1.333f, 1.5f, 2.f}) {
        for (Float alpha : {.04f, .15f, .4f, .8f}) {
            TrowbridgeReitzDistribution mf(alpha, alpha);
            for (Float cosTheta : {.95f, .65f, .3f}) {
                Vector3f wo(SafeSqrt(1 - Sqr(cosTheta)), 0, cosTheta);
                constexpr int n = 65536;
                for (int i = 1; i <= n; ++i) {
                    Float uc = ReferenceRoughTraceFract(Float(i) * .7548776662466927f);
                    Point2f u(ReferenceRoughTraceFract(Float(i) * .5698402909980532f),
                              ReferenceRoughTraceFract(Float(i) * .4385790218125324f));
                    Vector3f wm = mf.Sample_wm(wo, u);
                    Float R = FrDielectric(Dot(wo, wm), eta);
                    Float T = 1 - R;
                    Float pr = R, pt = T;
                    if (uc < pr / (pr + pt))
                        continue;

                    Float etap = 0;
                    Vector3f wi(0, 0, 0);
                    bool tir = !Refract(wo, (Normal3f)wm, eta, &etap, &wi);
                    if (tir || SameHemisphere(wo, wi) || wi.z == 0)
                        continue;

                    Float dotIM = AbsDot(wi, wm);
                    Float dotOM = Dot(wo, wm);
                    Float sum = Dot(wi, wm) + dotOM / etap;
                    Float denom = Sqr(sum);
                    Float dwm_dwi = dotIM / denom;
                    Float mfPdf = mf.PDF(wo, wm);
                    Float branch = pt / (pr + pt);
                    Float pdf = mfPdf * dwm_dwi * branch;

                    bool bad = IsNaN(denom) || IsInf(denom) ||
                               IsNaN(dwm_dwi) || IsInf(dwm_dwi) ||
                               IsNaN(mfPdf) || IsInf(mfPdf) ||
                               IsNaN(branch) || IsInf(branch) ||
                               IsNaN(pdf) || IsInf(pdf);
                    if (bad) {
                        ADD_FAILURE()
                            << "rough transmission non-finite algebra"
                            << " eta=" << eta << " alpha=" << alpha
                            << " cosTheta=" << cosTheta << " i=" << i
                            << " uc=" << uc << " u=(" << u.x << "," << u.y << ")"
                            << " wo=" << wo << " wm=" << wm << " wi=" << wi
                            << " R=" << R << " T=" << T
                            << " etap=" << etap << " dotIM=" << dotIM
                            << " dotOM=" << dotOM << " sum=" << sum
                            << " denom=" << denom << " dwm_dwi=" << dwm_dwi
                            << " mfPdf=" << mfPdf << " branch=" << branch
                            << " pdf=" << pdf;
                        return;
                    }
                }
            }
        }
    }
}
