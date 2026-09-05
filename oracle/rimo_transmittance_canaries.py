#!/usr/bin/env python3
"""End-to-end canaries for RIMO's PBRT segment-transmittance metrology integrator.

These exercise parser -> medium/interface construction -> visibility -> Integrator::Tr
-> spectral film. A paired `calibration=true` render uses the identical film, pixel,
sampler seed, wavelength sequence and scene but returns unit spectral transmission.
Componentwise measured/calibration RGB is therefore a dimensionless sensor response
relative to equal-energy incident light. Gray Beer-Lambert cases have exact scalar
solutions without RGB-to-spectrum ambiguity.
"""
from __future__ import annotations
import argparse,hashlib,json,math,struct,subprocess,time
from pathlib import Path

class Failure(RuntimeError): pass

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(cmd,cwd,log):
    t=time.monotonic()
    with open(log,'w') as f:
        f.write('command='+' '.join(map(str,cmd))+'\n');f.flush()
        p=subprocess.run(list(map(str,cmd)),cwd=cwd,stdout=f,stderr=subprocess.STDOUT)
        f.write(f'\nexit={p.returncode}\nelapsed_s={time.monotonic()-t:.6f}\n')
    if p.returncode: raise Failure(f'command failed: {cmd}')
def read_pfm(p):
    with open(p,'rb') as f:
        magic=f.readline().strip(); w,h=map(int,f.readline().split()); scale=float(f.readline()); raw=f.read()
    ch=3 if magic==b'PF' else 1
    vals=struct.unpack(('<' if scale<0 else '>')+f'{w*h*ch}f',raw)
    return w,h,[tuple(vals[i:i+3]) if ch==3 else (vals[i],)*3 for i in range(0,len(vals),ch)]
def lum(x): return .2126*x[0]+.7152*x[1]+.0722*x[2]
def rel(a,b): return abs(a-b)/max(abs(b),1e-30)
def ratio(a,b):
    if min(b)<=0: raise Failure(f'nonpositive calibration RGB {b}')
    return tuple(x/y for x,y in zip(a,b))
def header(name,starts,ends,spp=128,calibration=False):
    n=len(starts); ss=' '.join(' '.join(map(str,p)) for p in starts); ee=' '.join(' '.join(map(str,p)) for p in ends)
    cal=' "bool calibration" [true]' if calibration else ''
    return f'''Camera "perspective" "float fov" [45]\nFilm "rgb" "integer xresolution" [{n}] "integer yresolution" [1] "string filename" ["{name}.exr"] "bool savefp16" [false]\nPixelFilter "box" "float xradius" [.5] "float yradius" [.5]\nSampler "sobol" "integer pixelsamples" [{spp}] "integer seed" [91] "string randomization" "owen"\nIntegrator "rimotransmittance" "point3 starts" [{ss}] "point3 ends" [{ee}]{cal}\nWorldBegin\n'''
def render(pbrt,imgtool,out,name,text):
    sp=out/f'{name}.pbrt';sp.write_text(text)
    run([pbrt,'--quiet',sp.name],out,out/f'{name}.render.log')
    run([imgtool,'convert',f'{name}.exr','--outfile',f'{name}.pfm'],out,out/f'{name}.convert.log')
    return read_pfm(out/f'{name}.pfm')
def render_pair(pbrt,imgtool,out,name,starts,ends,spp,body=''):
    a=render(pbrt,imgtool,out,name,header(name,starts,ends,spp,False)+body)
    cname=name+'_calibration'
    b=render(pbrt,imgtool,out,cname,header(cname,starts,ends,spp,True)+body)
    if a[:2]!=b[:2]: raise Failure(f'calibration resolution mismatch for {name}')
    return [ratio(x,y) for x,y in zip(a[2],b[2])]
def require_gray(name,rgb,expected,tol=.015):
    errs=[rel(v,expected) for v in rgb]
    if max(errs)>tol: raise Failure(f'{name}: measured={rgb} expected={expected:.9g} maxrel={max(errs):.6g}')
    return max(errs)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pbrt',type=Path,required=True);ap.add_argument('--imgtool',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--commit',default='unknown');a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);P=a.pbrt.resolve();I=a.imgtool.resolve()
    report={'schema':2,'commit':a.commit,'pbrt_sha256':sha(P),'imgtool_sha256':sha(I),'definition':'componentwise PBRT spectral-film response of Integrator::Tr divided by paired unit-transmission response, same sampler/wavelength sequence','canaries':{},'failures':[]}
    try:
        starts=[(-3,0,0),(0,-4,1),(1,2,-5)]; ends=[(3,0,0),(0,5,1),(1,2,7)]
        rgbs=render_pair(P,I,out,'tr_vacuum',starts,ends,128)
        er=max(require_gray(f'vacuum[{i}]',rgb,1,.002) for i,rgb in enumerate(rgbs))
        report['canaries']['vacuum_identity']={'measured_rgb':rgbs,'expected':1.0,'max_relative_error':er,'status':'pass'}

        sigma=.5; starts=[(0,0,-2)]; ends=[(0,0,2)]
        body=f'MakeNamedMedium "m" "string type" "homogeneous" "spectrum sigma_a" [300 {sigma} 830 {sigma}] "spectrum sigma_s" [300 0 830 0]\nMaterial "interface"\nMediumInterface "m" ""\nShape "sphere" "float radius" [1]\n'
        rgb=render_pair(P,I,out,'tr_absorb',starts,ends,128,body)[0]; expected=math.exp(-1); er=require_gray('absorption',rgb,expected,.012)
        report['canaries']['homogeneous_absorption']={'measured_rgb':rgb,'expected':expected,'max_relative_error':er,'status':'pass'}

        ss=.35
        body=f'MakeNamedMedium "m" "string type" "homogeneous" "spectrum sigma_a" [300 0 830 0] "spectrum sigma_s" [300 {ss} 830 {ss}]\nMaterial "interface"\nMediumInterface "m" ""\nShape "sphere" "float radius" [1]\n'
        rgb=render_pair(P,I,out,'tr_scatter',starts,ends,128,body)[0]; expected=math.exp(-2*ss); er=require_gray('scattering extinction',rgb,expected,.015)
        report['canaries']['homogeneous_scattering_extinction']={'measured_rgb':rgb,'expected':expected,'max_relative_error':er,'status':'pass'}

        sa=.2; sb=.5; starts=[(0,0,-3)]; ends=[(0,0,3)]
        body=f'MakeNamedMedium "A" "string type" "homogeneous" "spectrum sigma_a" [300 {sa} 830 {sa}] "spectrum sigma_s" [300 0 830 0]\nMakeNamedMedium "B" "string type" "homogeneous" "spectrum sigma_a" [300 {sb} 830 {sb}] "spectrum sigma_s" [300 0 830 0]\nAttributeBegin Material "interface" MediumInterface "A" "" Shape "sphere" "float radius" [2] AttributeEnd\nAttributeBegin Material "interface" MediumInterface "B" "A" Shape "sphere" "float radius" [1] AttributeEnd\n'
        rgb=render_pair(P,I,out,'tr_nested',starts,ends,256,body)[0]; expected=math.exp(-2*sa-2*sb); er=require_gray('nested media',rgb,expected,.018)
        report['canaries']['nested_medium_interfaces']={'measured_rgb':rgb,'expected':expected,'max_relative_error':er,'status':'pass'}

        starts=[(0,0,-2)]; ends=[(0,0,2)]; body='Material "diffuse" "rgb reflectance" [.5 .5 .5]\nShape "sphere" "float radius" [.5]\n'
        rgb=render_pair(P,I,out,'tr_opaque',starts,ends,32,body)[0]
        if max(abs(v) for v in rgb)>1e-7: raise Failure(f'opaque blocker not zero: {rgb}')
        report['canaries']['opaque_blocker']={'measured_rgb':rgb,'expected':0.0,'status':'pass'}
    except Failure as e:
        report['failures'].append(str(e))
    report['status']='pass' if not report['failures'] else 'fail'
    (out/'rimo-transmittance-canaries.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2));raise SystemExit(0 if report['status']=='pass' else 1)
if __name__=='__main__': main()
