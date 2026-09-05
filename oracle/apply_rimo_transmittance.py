#!/usr/bin/env python3
"""Batched straight-connection transmittance. Not refracted BSDF transmission.
Uses PBRT geometry, medium majorants and interface transitions, with independent
sample-derived randomization rather than endpoint-hashed shared shadow streams.
"""
import importlib.util
from pathlib import Path

def once(s,a,b):
    if s.count(a)!=1: raise RuntimeError(f'Expected one seam: {a[:80]}')
    return s.replace(a,b,1)
spec=importlib.util.spec_from_file_location('sensor_patch','oracle/apply_rimo_sensor_camera.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.CLASS=once(m.CLASS,'bool irradiance, Float offset)','bool irradiance, Float offset, bool segment)')
m.CLASS=once(m.CLASS,'irradiance(irradiance), offset(offset)','irradiance(irradiance), offset(offset), segment(segment)')
m.CLASS=once(m.CLASS,'    bool irradiance;','    bool irradiance;\n    bool segment;')
m.CLASS=once(m.CLASS,'        if (irradiance) {','''        if (segment) {
            return CameraRay{Ray(tr(origins[i]),tr(directions[i]),SampleTime(s.time),medium),SampledSpectrum(1)};
        }
        if (irradiance) {''')
m.METHODS=once(m.METHODS,'    bool irradiance=p.GetOneBool("irradiance",false);','    bool irradiance=p.GetOneBool("irradiance",false);\n    bool segment=p.GetOneBool("segment",false);\n    if (segment && irradiance) ErrorExit(loc,"Segment and irradiance modes conflict");')
m.METHODS=once(m.METHODS,'(!irradiance && !(angles[i]>0 && angles[i]<=Pi))','(!irradiance && !segment && !(angles[i]>0 && angles[i]<=Pi))')
m.METHODS=once(m.METHODS,'        directions[i]/=len;','        if (!segment) directions[i]/=len;')
m.METHODS=once(m.METHODS,'std::move(angles),irradiance,offset);','std::move(angles),irradiance,offset,segment);')
m.main()
h=Path('src/pbrt/cpu/integrators.h');c=Path('src/pbrt/cpu/integrators.cpp')
cls=r'''// RIMO v05 straight-connection transmittance; no refracted BSDF paths.
class RimoTransmittanceIntegrator : public RayIntegrator {
  public:
    using RayIntegrator::RayIntegrator;
    std::string ToString() const override { return "RimoTransmittanceIntegrator/1"; }
    SampledSpectrum Li(RayDifferential ray, SampledWavelengths &lambda,
        Sampler sampler, ScratchBuffer &, VisibleSurface *) const override;
};

'''
impl=r'''// RIMO independently randomized spectral ratio-tracking measurement.
SampledSpectrum RimoTransmittanceIntegrator::Li(RayDifferential input,
    SampledWavelengths &lambda, Sampler sampler, ScratchBuffer &, VisibleSurface *) const {
    Interaction p0(input.o, input.time, input.medium);
    Interaction p1(input.o + input.d, input.time, Medium());
    Ray ray = p0.SpawnRayTo(p1);
    if (LengthSquared(ray.d)==0) return SampledSpectrum(1);
    uint64_t s0=uint64_t(sampler.Get1D()*Float(4294967296.0));
    uint64_t s1=uint64_t(sampler.Get1D()*Float(4294967296.0));
    RNG rng(Hash(s0,s1),Hash(s1,s0));
    SampledSpectrum tr(1);
    for (int crossings=0; ; ++crossings) {
        if (crossings>100000) ErrorExit("RIMO transmittance excessive interface crossings");
        auto hit=Intersect(ray, 1-ShadowEpsilon);
        if (hit && hit->intr.material) return SampledSpectrum(0);
        if (ray.medium) {
            Ray segment=ray;
            segment.d=(ray(hit ? hit->tHit : Float(1)))-ray.o;
            SampledSpectrum tail=SampleT_maj(segment,Float(1),rng.Uniform<Float>(),rng,lambda,
                [&](Point3f, MediumProperties mp, SampledSpectrum majorant, SampledSpectrum tm) {
                    Float proposal=tm[0]*majorant[0];
                    if (!(proposal>0)) ErrorExit("RIMO ratio-tracking proposal underflow");
                    tr *= tm * ClampZero(majorant-mp.sigma_a-mp.sigma_s) / proposal;
                    return bool(tr);
                });
            if (!tr) return SampledSpectrum(0);
            if (!(tail[0]>0)) ErrorExit("RIMO transmittance tail underflow");
            tr *= tail/tail[0];
        }
        if (!hit) return tr;
        ray=hit->intr.SpawnRayTo(p1);
    }
}

'''
h.write_text(once(h.read_text(),'// RandomWalkIntegrator Definition',cls+'// RandomWalkIntegrator Definition'))
s=c.read_text();s=once(s,'// RandomWalkIntegrator Method Definitions',impl+'// RandomWalkIntegrator Method Definitions')
start=s.index('std::unique_ptr<Integrator> Integrator::Create(');brace=s.index('{',start)+1
s=s[:brace]+'\n    if (name == "rimo_transmittance") return std::make_unique<RimoTransmittanceIntegrator>(camera,sampler,aggregate,lights);\n'+s[brace:];c.write_text(s)
print('RIMO transmittance + segment camera installed')
