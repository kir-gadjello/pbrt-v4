#!/usr/bin/env python3
"""Guarded source patch for RIMO's PBRT segment-transmittance integrator.

The integrator directly invokes PBRT Integrator::Tr between authored endpoints.
It therefore exercises ordinary scene intersection, opaque blocking, null material
interfaces, and PBRT medium transmittance without camera-image ratios. Segment
starts are currently required to be in the exterior/vacuum medium; crossing any
number of correctly authored null medium interfaces is supported.
"""
from pathlib import Path

H=Path('src/pbrt/cpu/integrators.h')
C=Path('src/pbrt/cpu/integrators.cpp')
h=H.read_text(); c=C.read_text()
if 'class RimoTransmittanceIntegrator' in h or 'name == "rimotransmittance"' in c:
    raise SystemExit('RIMO transmittance integrator already installed')

HM='// RandomWalkIntegrator Definition\n'
if h.count(HM)!=1: raise SystemExit('unexpected integrators.h insertion marker')
DECL=r'''// RimoTransmittanceIntegrator Definition
//
// Direct metrology sensor for unscattered segment transmittance. Each film pixel
// maps to one authored start/end pair. The estimator calls Integrator::Tr, so it
// uses the same opaque blocking, null-interface traversal, and medium transport
// machinery as PBRT's lighting paths. The start point is exterior/vacuum in v1;
// nested media entered through authored interfaces are fully traversed.
class RimoTransmittanceIntegrator : public ImageTileIntegrator {
  public:
    RimoTransmittanceIntegrator(std::vector<Point3f> starts, std::vector<Point3f> ends,
                                Camera camera, Sampler sampler, Primitive aggregate,
                                std::vector<Light> lights)
        : ImageTileIntegrator(camera, sampler, aggregate, lights),
          starts(std::move(starts)), ends(std::move(ends)) {}

    static std::unique_ptr<RimoTransmittanceIntegrator> Create(
        const ParameterDictionary &parameters, Camera camera, Sampler sampler,
        Primitive aggregate, std::vector<Light> lights, const FileLoc *loc);

    void EvaluatePixelSample(Point2i pPixel, int sampleIndex, Sampler sampler,
                             ScratchBuffer &scratchBuffer) override;
    std::string ToString() const override;

  private:
    std::vector<Point3f> starts, ends;
};

'''
h=h.replace(HM,DECL+HM)

CM='// RandomWalkIntegrator Method Definitions\n'
if c.count(CM)!=1: raise SystemExit('unexpected integrators.cpp method marker')
IMPL=r'''// RimoTransmittanceIntegrator Method Definitions
std::unique_ptr<RimoTransmittanceIntegrator> RimoTransmittanceIntegrator::Create(
    const ParameterDictionary &parameters, Camera camera, Sampler sampler,
    Primitive aggregate, std::vector<Light> lights, const FileLoc *loc) {
    std::vector<Point3f> starts = parameters.GetPoint3fArray("starts");
    std::vector<Point3f> ends = parameters.GetPoint3fArray("ends");
    Point2i resolution = camera.GetFilm().FullResolution();
    size_t expected = size_t(resolution.x) * size_t(resolution.y);
    if (starts.empty() || starts.size() != ends.size() || starts.size() != expected)
        ErrorExit(loc, "RIMO transmittance starts/ends must match film pixel count");
    for (size_t i = 0; i < starts.size(); ++i) {
        if (starts[i].HasNaN() || ends[i].HasNaN() || DistanceSquared(starts[i], ends[i]) == 0)
            ErrorExit(loc, "RIMO transmittance segment %d is non-finite or zero length", int(i));
    }
    return std::make_unique<RimoTransmittanceIntegrator>(
        std::move(starts), std::move(ends), camera, sampler, aggregate, lights);
}

void RimoTransmittanceIntegrator::EvaluatePixelSample(
    Point2i pPixel, int sampleIndex, Sampler sampler, ScratchBuffer &) {
    Point2i resolution = camera.GetFilm().FullResolution();
    int index = pPixel.x + resolution.x * pPixel.y;
    CHECK_GE(index, 0); CHECK_LT(index, int(starts.size()));

    Float lu = sampler.Get1D();
    if (Options->disableWavelengthJitter) lu = .5f;
    SampledWavelengths lambda = camera.GetFilm().SampleWavelengths(lu);
    Filter filter = camera.GetFilm().GetFilter();
    CameraSample cs = GetCameraSample(sampler, pPixel, filter);

    // Endpoint interactions are non-surface points in the exterior medium.
    // Integrator::Tr discovers and traverses subsequent null medium interfaces.
    Interaction p0(starts[index], Float(0), Medium(nullptr));
    Interaction p1(ends[index], Float(0), Medium(nullptr));
    SampledSpectrum T = Tr(p0, p1, lambda);
    if (T.HasNaNs()) {
        LOG_ERROR("NaN RIMO transmittance at pixel (%d,%d), sample %d", pPixel.x, pPixel.y,
                  sampleIndex);
        T = SampledSpectrum(0.f);
    }
    VisibleSurface visibleSurface;
    camera.GetFilm().AddSample(pPixel, T, lambda, &visibleSurface, cs.filterWeight);
}

std::string RimoTransmittanceIntegrator::ToString() const {
    return StringPrintf("[ RimoTransmittanceIntegrator segments: %d ]", int(starts.size()));
}

'''
c=c.replace(CM,IMPL+CM)

OLD='''    else if (name == "irradiance")\n        integrator = IrradianceIntegrator::Create(parameters, camera, sampler, aggregate,\n                                                  lights, loc);\n'''
NEW=OLD+'''    else if (name == "rimotransmittance")\n        integrator = RimoTransmittanceIntegrator::Create(parameters, camera, sampler, aggregate,\n                                                         lights, loc);\n'''
if c.count(OLD)!=1: raise SystemExit('unexpected Integrator::Create irradiance marker')
c=c.replace(OLD,NEW)
H.write_text(h); C.write_text(c)
print('installed RIMO transmittance integrator')
