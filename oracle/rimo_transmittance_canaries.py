#!/usr/bin/env python3
"""End-to-end canaries for RIMO's PBRT segment-transmittance metrology integrator.

These exercise parser -> medium/interface construction -> visibility -> Integrator::Tr
-> spectral film. All expected values are gray closed-form Beer-Lambert or exact
opaque/vacuum invariants, avoiding RGB-to-spectrum ambiguity.
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
def header(name,starts,ends,spp=128):
    n=len(starts); ss=' '.join(' '.join(map(str,p)) for p in starts); ee=' '.join(' '.join(map(str,p)) for p in ends)
    return f'''Camera "perspective" "float fov" [45]\nFilm "rgb" "integer xresolution" [{n}] "integer yresolution" [1] "string filename" ["{name}.exr"] "bool savefp16" [false]\nPixelFilter "box" "float xradius" [.5] "float yradius" [.5]\nSampler "sobol" "integer pixelsamples" [{spp}] "integer seed" [91] "string randomization" "owen"\nIntegrator "rimotransmittance" "point3 starts" [{ss}] "point3 ends" [{ee}]\nWorldBegin\n'''
def render(pbrt,imgtool,out,name,text):
    sp=out/f'{name}.pbrt';sp.write_text(text)
    run([pbrt,'--quiet',sp.name],out,out/f'{name}.render.log')
    run([imgtool,'convert',f'{name}.exr','--outfile',f'{name}.pfm'],out,out/f'{name}.convert.log')
    return read_pfm(out/f'{name}.pfm')
def require_rel(name,v,e,tol=.015):
    if rel(v,e)>tol: raise Failure(f'{name}: measured={v:.9g} expected={e:.9g} rel={rel(v,e):.6g}')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pbrt',type=Path,required=True);ap.add_argument('--imgtool',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--commit',default='unknown');a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);P=a.pbrt.resolve();I=a.imgtool.resolve()
    report={'schema':1,'commit':a.commit,'pbrt_sha256':sha(P),'imgtool_sha256':sha(I),'definition':'PBRT Integrator::Tr between exterior/vacuum segment endpoints; traverses authored null medium interfaces','canaries':{},'failures':[]}
    try:
        # Vacuum identity on multiple segment directions and lengths.
        starts=[(-3,0,0),(0,-4,1),(1,2,-5)]; ends=[(3,0,0),(0,5,1),(1,2,7)]
        x=render(P,I,out,'tr_vacuum',header('tr_vacuum',starts,ends,32)); vals=[lum(v) for v in x[2]]
        if max(rel(v,1) for v in vals)>.006: raise Failure(f'vacuum mismatch {vals}')
        report['canaries']['vacuum_identity']={'measured':vals,'expected':1.0,'status':'pass'}

        # Two-unit chord in absorbing sphere: exp(-sigma_a * 2).
        sigma=.5; text=header('tr_absorb',[ (0,0,-2) ],[ (0,0,2) ],128)
        text+=f'MakeNamedMedium "m" "string type" "homogeneous" "spectrum sigma_a" [300 {sigma} 830 {sigma}] "spectrum sigma_s" [300 0 830 0]\n'
        text+='Material "interface"\nMediumInterface "m" ""\nShape "sphere" "float radius" [1]\n'
        v=lum(render(P,I,out,'tr_absorb',text)[2][0]); expected=math.exp(-1)
        require_rel('absorption',v,expected,.012)
        report['canaries']['homogeneous_absorption']={'measured':v,'expected':expected,'relative_error':rel(v,expected),'status':'pass'}

        # Extinction includes scattering in unscattered transmittance.
        ss=.35; text=header('tr_scatter',[ (0,0,-2) ],[ (0,0,2) ],128)
        text+=f'MakeNamedMedium "m" "string type" "homogeneous" "spectrum sigma_a" [300 0 830 0] "spectrum sigma_s" [300 {ss} 830 {ss}]\n'
        text+='Material "interface"\nMediumInterface "m" ""\nShape "sphere" "float radius" [1]\n'
        v=lum(render(P,I,out,'tr_scatter',text)[2][0]); expected=math.exp(-2*ss)
        require_rel('scattering extinction',v,expected,.015)
        report['canaries']['homogeneous_scattering_extinction']={'measured':v,'expected':expected,'relative_error':rel(v,expected),'status':'pass'}

        # Nested medium interface: A over two outer one-unit shells, B over two inner units.
        sa=.2; sb=.5; text=header('tr_nested',[ (0,0,-3) ],[ (0,0,3) ],256)
        text+=f'MakeNamedMedium "A" "string type" "homogeneous" "spectrum sigma_a" [300 {sa} 830 {sa}] "spectrum sigma_s" [300 0 830 0]\n'
        text+=f'MakeNamedMedium "B" "string type" "homogeneous" "spectrum sigma_a" [300 {sb} 830 {sb}] "spectrum sigma_s" [300 0 830 0]\n'
        text+='AttributeBegin Material "interface" MediumInterface "A" "" Shape "sphere" "float radius" [2] AttributeEnd\n'
        text+='AttributeBegin Material "interface" MediumInterface "B" "A" Shape "sphere" "float radius" [1] AttributeEnd\n'
        v=lum(render(P,I,out,'tr_nested',text)[2][0]); expected=math.exp(-2*sa-2*sb)
        require_rel('nested media',v,expected,.018)
        report['canaries']['nested_medium_interfaces']={'measured':v,'expected':expected,'relative_error':rel(v,expected),'status':'pass'}

        # Material-bearing surface blocks the segment exactly.
        text=header('tr_opaque',[ (0,0,-2) ],[ (0,0,2) ],16)
        text+='Material "diffuse" "rgb reflectance" [.5 .5 .5]\nShape "sphere" "float radius" [.5]\n'
        v=lum(render(P,I,out,'tr_opaque',text)[2][0])
        if abs(v)>1e-7: raise Failure(f'opaque blocker not zero: {v}')
        report['canaries']['opaque_blocker']={'measured':v,'expected':0.0,'status':'pass'}
    except Failure as e:
        report['failures'].append(str(e))
    report['status']='pass' if not report['failures'] else 'fail'
    (out/'rimo-transmittance-canaries.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2));raise SystemExit(0 if report['status']=='pass' else 1)
if __name__=='__main__': main()
