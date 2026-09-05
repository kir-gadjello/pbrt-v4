#!/usr/bin/env python3
"""Analytic end-to-end canaries for RIMO's batched PBRT sensor camera.

Surface irradiance uses the qualified PBRT IrradianceIntegrator. Cone radiance uses
ordinary path transport through finite angular camera footprints. The canaries include
uniform-environment orientation invariance and an inverse-square delta point-light test.
"""
from __future__ import annotations
import argparse,hashlib,json,math,struct,subprocess,time
from pathlib import Path

class Failure(RuntimeError):pass

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(cmd,cwd,log):
 t=time.monotonic()
 with open(log,'w') as f:
  f.write('command='+' '.join(map(str,cmd))+'\n');f.flush();p=subprocess.run(list(map(str,cmd)),cwd=cwd,stdout=f,stderr=subprocess.STDOUT);f.write(f'\nexit={p.returncode}\nelapsed_s={time.monotonic()-t:.6f}\n')
 if p.returncode:raise Failure(f'command failed: {cmd}')
def read_pfm(p):
 with open(p,'rb') as f:
  magic=f.readline().strip();w,h=map(int,f.readline().split());scale=float(f.readline());raw=f.read()
 ch=3 if magic==b'PF' else 1;vals=struct.unpack(('<' if scale<0 else '>')+f'{w*h*ch}f',raw)
 return w,h,[tuple(vals[i:i+3]) if ch==3 else (vals[i],)*3 for i in range(0,len(vals),ch)]
def lum(x):return .2126*x[0]+.7152*x[1]+.0722*x[2]
def camera_block(name,irradiance,origins,dirs,angles,spp=256,integrator='path',component=None):
 n=len(dirs);oo=' '.join(' '.join(map(str,p)) for p in origins);dd=' '.join(' '.join(map(str,d)) for d in dirs);aa=' '.join(map(str,angles));comp=f' "string component" ["{component}"]' if component else ''
 return f'''Camera "rimosensor"
 "point3 origins" [{oo}]
 "vector3 directions" [{dd}]
 "float halfangles" [{aa}]
 "bool irradiance" [{str(irradiance).lower()}]
 "float surfaceoffset" [0.0001]
Film "rgb" "integer xresolution" [{n}] "integer yresolution" [1]
 "string filename" ["{name}.exr"] "bool savefp16" [false]
PixelFilter "box" "float xradius" [.5] "float yradius" [.5]
Sampler "sobol" "integer pixelsamples" [{spp}] "integer seed" [73] "string randomization" "owen"
Integrator "{integrator}" "integer maxdepth" [4]{comp}
WorldBegin
'''
def env_irradiance_scene(name):
 origins=[(0,0,1),(101,0,0),(200,1,0)];dirs=[(0,0,1),(1,0,0),(0,1,0)]
 s=camera_block(name,True,origins,dirs,[0,0,0],512,'irradiance','total')
 s+='LightSource "infinite" "rgb L" [1 1 1]\nMaterial "diffuse" "rgb reflectance" [0 0 0]\n'
 s+='AttributeBegin Translate 0 0 0 Shape "sphere" "float radius" [1] AttributeEnd\n'
 s+='AttributeBegin Translate 100 0 0 Shape "sphere" "float radius" [1] AttributeEnd\n'
 s+='AttributeBegin Translate 200 0 0 Shape "sphere" "float radius" [1] AttributeEnd\n'
 return s
def point_irradiance_scene(name):
 s=camera_block(name,True,[(0,0,0)],[(0,0,1)],[0],512,'irradiance','direct')
 s+='LightSource "point" "rgb I" [1 1 1] "point3 from" [0 0 2]\n'
 s+='Material "diffuse" "rgb reflectance" [0 0 0]\nShape "disk" "float radius" [10]\n'
 return s
def cone_scene(name):
 s=camera_block(name,False,[(0,0,0)]*3,[(0,0,1),(1,0,0),(0,1,0)],[.01,.2,.9],256,'path')
 return s+'LightSource "infinite" "rgb L" [1 1 1]\n'
def render(pbrt,imgtool,out,name,text):
 sp=out/f'{name}.pbrt';sp.write_text(text);run([pbrt,sp.name],out,out/f'{name}.render.log');run([imgtool,'convert',f'{name}.exr','--outfile',f'{name}.pfm'],out,out/f'{name}.convert.log');return read_pfm(out/f'{name}.pfm')
def rel(a,b):return abs(a-b)/max(abs(b),1e-30)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pbrt',type=Path,required=True);ap.add_argument('--imgtool',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--commit',default='unknown');a=ap.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);P=a.pbrt.resolve();I=a.imgtool.resolve();report={'schema':2,'commit':a.commit,'pbrt_sha256':sha(P),'imgtool_sha256':sha(I),'canaries':{},'failures':[]}
 try:
  e=render(P,I,out,'rimo_irradiance',env_irradiance_scene('rimo_irradiance'));vals=[lum(x) for x in e[2]]
  er=max(rel(v,math.pi) for v in vals)
  if er>.015:raise Failure(f'irradiance/pi mismatch {vals}')
  report['canaries']['uniform_environment_surface_irradiance']={'measured':vals,'expected':math.pi,'max_relative_error':er,'status':'pass'}
  p=render(P,I,out,'rimo_point_irradiance',point_irradiance_scene('rimo_point_irradiance'));pv=lum(p[2][0]);expected=.25
  if rel(pv,expected)>.015:raise Failure(f'point-light irradiance mismatch {pv}')
  report['canaries']['point_light_inverse_square_irradiance']={'measured':pv,'expected':expected,'relative_error':rel(pv,expected),'status':'pass'}
  c=render(P,I,out,'rimo_cone',cone_scene('rimo_cone'));vals=[lum(x) for x in c[2]]
  if max(rel(v,1) for v in vals)>.008:raise Failure(f'cone/unit-radiance mismatch {vals}')
  if max(vals)-min(vals)>.008:raise Failure(f'cone/orientation invariance mismatch {vals}')
  report['canaries']['uniform_environment_cone_radiance']={'measured':vals,'expected':1.0,'max_relative_error':max(rel(v,1) for v in vals),'status':'pass'}
 except Failure as e: report['failures'].append(str(e))
 report['status']='pass' if not report['failures'] else 'fail';(out/'rimo-sensor-canaries.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps(report,indent=2));raise SystemExit(0 if report['status']=='pass' else 1)
if __name__=='__main__':main()
