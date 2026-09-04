#!/usr/bin/env python3
"""Guarded one-shot source patch for the camera-conditioned irradiance integrator.

This script is intentionally strict: it patches exactly the qualified PBRT CPU
integrator seams and refuses to run if the expected upstream/reference text has
moved.  The resulting C++ sources, not this patcher, are the durable implementation.
"""
from pathlib import Path

H = Path("src/pbrt/cpu/integrators.h")
C = Path("src/pbrt/cpu/integrators.cpp")

h = H.read_text()
c = C.read_text()

if "class IrradianceIntegrator" in h or 'name == "irradiance"' in c:
    raise SystemExit("irradiance integrator already present; refusing to patch twice")

header_marker = "// SimpleVolPathIntegrator Definition\n"
if h.count(header_marker) != 1:
    raise SystemExit("unexpected integrators.h marker count")

irr_header = r'''// IrradianceIntegrator Definition
//
// Measures hemispherical irradiance E = integral_H Li(w) cos(theta) dw at the
// first material-bearing surface visible to each camera sample.  The sensor
// surface itself is not a scattering vertex.  A cosine-hemisphere sample
// continues through the ordinary PBRT surface path tracer, while next-event
// light sampling at the sensor is combined with it using MIS.
class IrradianceIntegrator : public RayIntegrator {
  public:
    IrradianceIntegrator(int maxDepth, bool directOnly, Camera camera, Sampler sampler,
                         Primitive aggregate, std::vector<Light> lights,
                         const std::string &lightSampleStrategy = "bvh",
                         bool regularize = false)
        : RayIntegrator(camera, sampler, aggregate, lights),
          maxDepth(maxDepth),
          directOnly(directOnly),
          lightSampler(LightSampler::Create(lightSampleStrategy, lights, Allocator())),
          regularize(regularize) {}

    SampledSpectrum Li(RayDifferential ray, SampledWavelengths &lambda, Sampler sampler,
                       ScratchBuffer &scratchBuffer,
                       VisibleSurface *visibleSurface) const;

    static std::unique_ptr<IrradianceIntegrator> Create(
        const ParameterDictionary &parameters, Camera camera, Sampler sampler,
        Primitive aggregate, std::vector<Light> lights, const FileLoc *loc);

    std::string ToString() const;

  private:
    SampledSpectrum SampleSensorDirect(const SurfaceInteraction &intr, Normal3f n,
                                       SampledWavelengths &lambda,
                                       Sampler sampler) const;

    int maxDepth;
    bool directOnly;
    LightSampler lightSampler;
    bool regularize;
};

'''
h = h.replace(header_marker, irr_header + header_marker)

cpp_marker = "// SimpleVolPathIntegrator Method Definitions\n"
if c.count(cpp_marker) != 1:
    raise SystemExit("unexpected integrators.cpp insertion marker count")

irr_cpp = r'''// IrradianceIntegrator Method Definitions
SampledSpectrum IrradianceIntegrator::SampleSensorDirect(
    const SurfaceInteraction &intr, Normal3f n, SampledWavelengths &lambda,
    Sampler sampler) const {
    LightSampleContext ctx(intr);
    // The irradiance sensor is the camera-facing geometric side of the first
    // visible surface.  Nudge light queries to that side as well.
    ctx.pi = intr.OffsetRayOrigin(Vector3f(n));

    pstd::optional<SampledLight> sampledLight = lightSampler.Sample(ctx, sampler.Get1D());
    Point2f uLight = sampler.Get2D();
    if (!sampledLight)
        return SampledSpectrum(0.f);

    Light light = sampledLight->light;
    pstd::optional<LightLiSample> ls = light.SampleLi(ctx, uLight, lambda, true);
    if (!ls || !ls->L || ls->pdf == 0)
        return SampledSpectrum(0.f);

    Float cosTheta = Dot(ls->wi, n);
    if (cosTheta <= 0)
        return SampledSpectrum(0.f);

    Float p_l = sampledLight->p * ls->pdf;
    if (p_l == 0)
        return SampledSpectrum(0.f);

    // Use the general transmittance query rather than binary visibility so
    // null material boundaries and homogeneous media do not become false
    // blockers for direct irradiance.
    SampledSpectrum T = Tr(intr, ls->pLight, lambda);
    if (!T)
        return SampledSpectrum(0.f);

    SampledSpectrum f = T * ls->L * cosTheta;
    if (IsDeltaLight(light.Type()))
        return f / p_l;

    Float p_h = CosineHemispherePDF(cosTheta);
    Float w_l = PowerHeuristic(1, p_l, 1, p_h);
    return w_l * f / p_l;
}

SampledSpectrum IrradianceIntegrator::Li(RayDifferential ray,
                                         SampledWavelengths &lambda, Sampler sampler,
                                         ScratchBuffer &scratchBuffer,
                                         VisibleSurface *visibleSurf) const {
    // Find the first actual material surface.  Pure medium/interface boundaries
    // are traversal state, not irradiance sensors.
    pstd::optional<ShapeIntersection> sensorHit;
    while (true) {
        sensorHit = Intersect(ray);
        if (!sensorHit)
            return SampledSpectrum(0.f);
        if (sensorHit->intr.material)
            break;
        sensorHit->intr.SkipIntersection(&ray, sensorHit->tHit);
    }

    SurfaceInteraction &sensor = sensorHit->intr;
    Normal3f n = FaceForward(sensor.n, -ray.d);

    // GBuffer output is useful to align a DUT's irradiance field with the
    // oracle.  Irradiance is material-independent, hence the zero albedo here.
    if (visibleSurf)
        *visibleSurf = VisibleSurface(sensor, SampledSpectrum(0.f), lambda);

    SampledSpectrum L = SampleSensorDirect(sensor, n, lambda, sampler);

    // Cosine-importance sample the remaining sensor integral.  Since
    // p(w)=cos(theta)/Pi, the sensor throughput is exactly Pi.
    Vector3f wiLocal = SampleCosineHemisphere(sampler.Get2D());
    Float p_b = CosineHemispherePDF(wiLocal.z);
    if (p_b == 0)
        return L;
    Frame sensorFrame = Frame::FromZ(n);
    Vector3f wi = sensorFrame.FromLocal(wiLocal);
    RayDifferential pathRay(sensor.SpawnRay(wi));

    SampledSpectrum beta(Pi);
    int depth = 0;
    Float etaScale = 1;
    bool specularBounce = false, anyNonSpecularBounces = false;
    LightSampleContext prevIntrCtx(sensor);
    prevIntrCtx.pi = sensor.OffsetRayOrigin(Vector3f(n));

    while (true) {
        pstd::optional<ShapeIntersection> si = Intersect(pathRay);

        if (!si) {
            for (const auto &light : infiniteLights) {
                SampledSpectrum Le = light.Le(pathRay, lambda);
                if (!Le)
                    continue;
                if (specularBounce)
                    L += beta * Le;
                else {
                    Float p_l = lightSampler.PMF(prevIntrCtx, light) *
                                light.PDF_Li(prevIntrCtx, pathRay.d, true);
                    Float w_b = PowerHeuristic(1, p_b, 1, p_l);
                    L += beta * w_b * Le;
                }
            }
            break;
        }

        SampledSpectrum Le = si->intr.Le(-pathRay.d, lambda);
        if (Le) {
            if (specularBounce)
                L += beta * Le;
            else {
                Light areaLight(si->intr.areaLight);
                Float p_l = lightSampler.PMF(prevIntrCtx, areaLight) *
                            areaLight.PDF_Li(prevIntrCtx, pathRay.d, true);
                Float w_b = PowerHeuristic(1, p_b, 1, p_l);
                L += beta * w_b * Le;
            }
        }

        // Direct mode contains precisely the sensor NEE plus directly visible
        // radiance reached by the correlated cosine sample.  Everything after
        // the first real scattering vertex is indirect irradiance.
        if (directOnly)
            break;

        SurfaceInteraction &isect = si->intr;
        BSDF bsdf = isect.GetBSDF(pathRay, lambda, camera, scratchBuffer, sampler);
        if (!bsdf) {
            // Preserve PBRT's ordinary handling of null material boundaries.
            isect.SkipIntersection(&pathRay, si->tHit);
            continue;
        }

        if (regularize && anyNonSpecularBounces)
            bsdf.Regularize();

        if (depth++ == maxDepth)
            break;

        if (IsNonSpecular(bsdf.Flags())) {
            LightSampleContext ctx(isect);
            BxDFFlags flags = bsdf.Flags();
            if (IsReflective(flags) && !IsTransmissive(flags))
                ctx.pi = isect.OffsetRayOrigin(isect.wo);
            else if (IsTransmissive(flags) && !IsReflective(flags))
                ctx.pi = isect.OffsetRayOrigin(-isect.wo);

            pstd::optional<SampledLight> sampledLight =
                lightSampler.Sample(ctx, sampler.Get1D());
            Point2f uLight = sampler.Get2D();
            if (sampledLight) {
                Light light = sampledLight->light;
                pstd::optional<LightLiSample> ls =
                    light.SampleLi(ctx, uLight, lambda, true);
                if (ls && ls->L && ls->pdf > 0) {
                    Vector3f wo = isect.wo;
                    SampledSpectrum f = bsdf.f(wo, ls->wi) *
                                        AbsDot(ls->wi, isect.shading.n);
                    if (f) {
                        SampledSpectrum T = Tr(isect, ls->pLight, lambda);
                        if (T) {
                            Float p_l = sampledLight->p * ls->pdf;
                            if (IsDeltaLight(light.Type()))
                                L += beta * T * ls->L * f / p_l;
                            else {
                                Float p_bsdf = bsdf.PDF(wo, ls->wi);
                                Float w_l = PowerHeuristic(1, p_l, 1, p_bsdf);
                                L += beta * w_l * T * ls->L * f / p_l;
                            }
                        }
                    }
                }
            }
        }

        Vector3f wo = -pathRay.d;
        pstd::optional<BSDFSample> bs =
            bsdf.Sample_f(wo, sampler.Get1D(), sampler.Get2D());
        if (!bs)
            break;

        beta *= bs->f * AbsDot(bs->wi, isect.shading.n) / bs->pdf;
        p_b = bs->pdfIsProportional ? bsdf.PDF(wo, bs->wi) : bs->pdf;
        if (!beta || p_b == 0)
            break;
        specularBounce = bs->IsSpecular();
        anyNonSpecularBounces |= !bs->IsSpecular();
        if (bs->IsTransmission())
            etaScale *= Sqr(bs->eta);
        prevIntrCtx = LightSampleContext(isect);
        pathRay = isect.SpawnRay(pathRay, bsdf, bs->wi, bs->flags, bs->eta);

        SampledSpectrum rrBeta = beta * etaScale;
        if (rrBeta.MaxComponentValue() < 1 && depth > 1) {
            Float q = std::max<Float>(0, 1 - rrBeta.MaxComponentValue());
            if (sampler.Get1D() < q)
                break;
            beta /= 1 - q;
        }
    }

    return L;
}

std::string IrradianceIntegrator::ToString() const {
    return StringPrintf(
        "[ IrradianceIntegrator maxDepth: %d component: %s lightSampler: %s regularize: %s ]",
        maxDepth, directOnly ? "direct" : "total", lightSampler, regularize);
}

std::unique_ptr<IrradianceIntegrator> IrradianceIntegrator::Create(
    const ParameterDictionary &parameters, Camera camera, Sampler sampler,
    Primitive aggregate, std::vector<Light> lights, const FileLoc *loc) {
    int maxDepth = parameters.GetOneInt("maxdepth", 5);
    std::string component = parameters.GetOneString("component", "total");
    if (component != "total" && component != "direct")
        ErrorExit(loc, "irradiance component must be \"total\" or \"direct\", got %s",
                  component);
    std::string lightStrategy = parameters.GetOneString("lightsampler", "bvh");
    bool regularize = parameters.GetOneBool("regularize", false);
    return std::make_unique<IrradianceIntegrator>(
        maxDepth, component == "direct", camera, sampler, aggregate, lights,
        lightStrategy, regularize);
}

'''
c = c.replace(cpp_marker, irr_cpp + cpp_marker)

factory_old = '''    if (name == "path")\n        integrator =\n            PathIntegrator::Create(parameters, camera, sampler, aggregate, lights, loc);\n    else if (name == "function")\n'''
factory_new = '''    if (name == "path")\n        integrator =\n            PathIntegrator::Create(parameters, camera, sampler, aggregate, lights, loc);\n    else if (name == "irradiance")\n        integrator = IrradianceIntegrator::Create(parameters, camera, sampler, aggregate,\n                                                  lights, loc);\n    else if (name == "function")\n'''
if c.count(factory_old) != 1:
    raise SystemExit("unexpected integrator factory seam")
c = c.replace(factory_old, factory_new)

H.write_text(h)
C.write_text(c)
print("patched IrradianceIntegrator into", H, "and", C)
