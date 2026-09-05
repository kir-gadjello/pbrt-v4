#!/usr/bin/env python3
"""Batched straight-connection transmittance. Not refracted BSDF transmission.
Uses PBRT geometry, generic medium majorants and interface transitions. A scalar
maximum-channel proposal guarantees event support even when one wavelength has
zero extinction; each channel accumulates its independent physical ratio factor.
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
cls=r'''// RIMO straight-connection transmittance; no refracted BSDF paths.
class RimoTransmittanceIntegrator : public RayIntegrator {
  public:
    using RayIntegrator::RayIntegrator;
    std::string ToString() const override { return "RimoTransmittanceIntegrator/2"; }
    SampledSpectrum Li(RayDifferential ray, SampledWavelengths &lambda,
        Sampler sampler, ScratchBuffer &, VisibleSurface *) const override;
};

'''
impl=r'''// Scalar-proposal spectral Poisson ratio tracking.
// E[product(1-sigma(lambda,x)/M(x))] = exp(-integral sigma(lambda,x) dx).
// M is the maximum majorant across sampled wavelengths, not wavelength zero.
SampledSpectrum RimoTransmittanceIntegrator::Li(RayDifferential input,
    SampledWavelengths &lambda, Sampler sampler, ScratchBuffer &scratch, VisibleSurface *) const {
    Interaction p0(input.o, input.time, input.medium);
    Interaction p1(input.o + input.d, input.time, Medium());
    Ray ray = p0.SpawnRayTo(p1);
    if (LengthSquared(ray.d)==0) return SampledSpectrum(1);
    uint64_t s0=uint64_t(sampler.Get1D()*Float(4294967296.0));
    uint64_t s1=uint64_t(sampler.Get1D()*Float(4294967296.0));
    RNG rng(Hash(s0,s1),Hash(s1,s0));
    SampledSpectrum tr(1);
    uint64_t events=0;
    for (int crossings=0; ; ++crossings) {
        if (crossings>100000) ErrorExit("RIMO transmittance excessive interface crossings");
        auto hit=Intersect(ray, 1-ShadowEpsilon);
        if (hit && hit->intr.material) return SampledSpectrum(0);
        if (ray.medium) {
            Float distance=Length(ray.d)*(hit ? hit->tHit : Float(1));
            Ray segment(ray.o,Normalize(ray.d),ray.time,ray.medium);
            RayMajorantIterator iterator=segment.medium.SampleRay(segment,distance,lambda,scratch);
            while (auto span=iterator.Next()) {
                Float M=span->sigma_maj.MaxComponentValue();
                if (M<0 || IsInf(M) || IsNaN(M)) ErrorExit("RIMO invalid medium majorant");
                if (M==0) continue;
                Float t=span->tMin;
                while (true) {
                    Float next=t+SampleExponential(rng.Uniform<Float>(),M);
                    if (next<=t) next=NextFloatUp(t);
                    if (next>=span->tMax) break;
                    t=next;
                    if (++events>100000000) ErrorExit("RIMO transmittance event budget exhausted");
                    MediumProperties mp=segment.medium.SamplePoint(segment(t),lambda);
                    SampledSpectrum sigma=mp.sigma_a+mp.sigma_s;
                    Float tolerance=64*std::numeric_limits<Float>::epsilon()*std::max(Float(1),M);
                    for (int i=0;i<NSpectrumSamples;++i) {
                        if (IsInf(sigma[i]) || IsNaN(sigma[i]) || sigma[i]<-tolerance || sigma[i]>M+tolerance)
                            ErrorExit("RIMO medium majorant does not bound extinction");
                        tr[i]*=std::clamp(1-sigma[i]/M,Float(0),Float(1));
                    }
                    if (!tr) return SampledSpectrum(0);
                }
            }
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
