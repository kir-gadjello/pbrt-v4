#!/usr/bin/env python3
"""Deterministically install RIMO's CPU batched sensor camera into the pinned PBRT tree.

Surface-irradiance mode only lands a camera ray on the declared geometric side;
PBRT's independently qualified IrradianceIntegrator owns the hemispherical NEE/MIS
estimator. Finite-cone mode samples outgoing camera rays over a declared cone.
The patch is intentionally narrow and fail-closed: every expected upstream seam
must occur exactly once. CI records this patcher and resulting binary hashes.
"""
from pathlib import Path

BASE=Path('src/pbrt/base/camera.h')
HEADER=Path('src/pbrt/cameras.h')
CPP=Path('src/pbrt/cameras.cpp')

def once(text,old,new):
    if text.count(old)!=1:
        raise RuntimeError(f'PBRT seam count={text.count(old)} expected=1: {old[:72]}')
    return text.replace(old,new,1)

CLASS=r'''// RIMO CPU batched sensor camera.
class RimoSensorCamera : public CameraBase {
  public:
    RimoSensorCamera(CameraBaseParameters b, std::vector<Point3f> o,
                     std::vector<Vector3f> d, std::vector<Float> a,
                     bool irradiance, Float offset)
        : CameraBase(b), origins(std::move(o)), directions(std::move(d)),
          angles(std::move(a)), irradiance(irradiance), offset(offset) {
        minPosDifferentialX = minPosDifferentialY = Vector3f(0,0,0);
        minDirDifferentialX = minDirDifferentialY = Vector3f(0,0,0);
    }
    static RimoSensorCamera *Create(const ParameterDictionary &,
                                    const CameraTransform &, Film, Medium,
                                    const FileLoc *, Allocator);
    PBRT_CPU_GPU pstd::optional<CameraRay> GenerateRay(
        CameraSample s, SampledWavelengths &) const {
        int x=int(std::floor(s.pFilm.x)), y=int(std::floor(s.pFilm.y));
        int i=x+y*film.FullResolution().x;
        if(x<0 || y<0 || x>=film.FullResolution().x || y>=film.FullResolution().y ||
           i<0 || i>=int(origins.size())) return {};
        const Transform tr=cameraTransform.RenderFromWorld();
        if (irradiance) {
            // Sensor origins are exact world-space surface points and directions are
            // outward geometric-side normals. Start just outside and point inward;
            // the IrradianceIntegrator then measures the camera-facing side of the
            // first material-bearing hit, including delta-light direct terms.
            Point3f origin=origins[i]+offset*directions[i];
            Ray ray(tr(origin),tr(-directions[i]),SampleTime(s.time),medium);
            return CameraRay{ray,SampledSpectrum(1)};
        }
        Float u=s.pLens.x, v=s.pLens.y;
        Float z=1-u*(1-std::cos(angles[i]));
        Float rr=std::sqrt(std::max<Float>(0,1-z*z)), phi=2*Pi*v;
        Vector3f local(rr*std::cos(phi),rr*std::sin(phi),z);
        Vector3f dir=Normalize(Frame::FromZ(directions[i]).FromLocal(local));
        Ray ray(tr(origins[i]),tr(dir),SampleTime(s.time),medium);
        return CameraRay{ray,SampledSpectrum(1)};
    }
    PBRT_CPU_GPU pstd::optional<CameraRayDifferential> GenerateRayDifferential(
        CameraSample s, SampledWavelengths &l) const {
        auto r=GenerateRay(s,l); if(!r) return {};
        RayDifferential rd(r->ray); rd.hasDifferentials=false;
        return CameraRayDifferential{rd,r->weight};
    }
    PBRT_CPU_GPU SampledSpectrum We(const Ray &,SampledWavelengths &,
                                    Point2f * = nullptr) const {
        LOG_FATAL("RIMO sensor camera does not support light-to-camera BDPT connections");
        return {};
    }
    PBRT_CPU_GPU void PDF_We(const Ray &,Float *,Float *) const {
        LOG_FATAL("RIMO sensor PDF_We unsupported");
    }
    PBRT_CPU_GPU pstd::optional<CameraWiSample> SampleWi(
        const Interaction &,Point2f,SampledWavelengths &) const {
        LOG_FATAL("RIMO sensor SampleWi unsupported"); return {};
    }
    std::string ToString() const { return "RimoSensorCamera/2 CPU surface-hit/cone sensor"; }
  private:
    std::vector<Point3f> origins;
    std::vector<Vector3f> directions;
    std::vector<Float> angles;
    bool irradiance;
    Float offset;
};

'''

METHODS=r'''// RIMO batched sensor camera creation.
RimoSensorCamera *RimoSensorCamera::Create(const ParameterDictionary &p,
    const CameraTransform &ct, Film film, Medium medium, const FileLoc *loc,
    Allocator alloc) {
    CameraBaseParameters base(ct,film,medium,p,loc);
    auto origins=p.GetPoint3fArray("origins");
    auto directions=p.GetVector3fArray("directions");
    auto angles=p.GetFloatArray("halfangles");
    bool irradiance=p.GetOneBool("irradiance",false);
    Float offset=p.GetOneFloat("surfaceoffset",1e-4);
    if(origins.empty() || origins.size()!=directions.size() ||
       angles.size()!=origins.size() ||
       origins.size()!=size_t(film.FullResolution().x)*film.FullResolution().y ||
       offset<0)
        ErrorExit(loc,"RIMO sensor arrays, film dimensions or offset invalid");
    for(size_t i=0;i<origins.size();i++) {
        Float len=Length(directions[i]);
        if(!std::isfinite(len) || len==0 ||
           (!irradiance && !(angles[i]>0 && angles[i]<=Pi)))
            ErrorExit(loc,"RIMO sensor direction/angle invalid");
        directions[i]/=len;
    }
    return alloc.new_object<RimoSensorCamera>(base,std::move(origins),
        std::move(directions),std::move(angles),irradiance,offset);
}

'''

def main():
    b=BASE.read_text(); h=HEADER.read_text(); c=CPP.read_text()
    if 'class RimoSensorCamera' in b or 'class RimoSensorCamera' in h:
        raise RuntimeError('RIMO sensor camera already installed')
    b=once(b,'class PerspectiveCamera;','class RimoSensorCamera;\nclass PerspectiveCamera;')
    b=once(b,'SphericalCamera, RealisticCamera>','SphericalCamera, RealisticCamera, RimoSensorCamera>')
    h=once(h,'// ProjectiveCamera Definition',CLASS+'// ProjectiveCamera Definition')
    c=once(c,'namespace pbrt {','namespace pbrt {\n\n'+METHODS)
    c=once(c,'    if (name == "perspective")\n',
        '    if (name == "rimosensor")\n'
        '        camera = RimoSensorCamera::Create(parameters, cameraTransform, film, medium, loc, alloc);\n'
        '    else if (name == "perspective")\n')
    BASE.write_text(b); HEADER.write_text(h); CPP.write_text(c)
    print('RIMO sensor camera patch applied')
if __name__=='__main__':main()
