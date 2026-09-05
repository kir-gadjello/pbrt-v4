#!/usr/bin/env python3
"""Analytic end-to-end canaries for RIMO's batched PBRT sensor camera."""
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
def scene(name,irradiance,dirs,angles,spp=256):
 n=len(dirs);origins=' '.join(['0 0 0']*n);directions=' '.join(' '.join(map(str,d)) for d in dirs);aa=' '.join(map(str,angles))
 return f'''Camera "rimosensor"
 "point3 origins" [{origins}]
 "vector3 directions" [{directions}]
 "float halfangles" [{aa}]
 "bool irradiance" [{str(irradiance).lower()}]
 "float surfaceoffset" [0]
Film "rgb" "integer xresolution" [{n}] "integer yresolution" [1]
 "string filename" ["{name}.exr"] "bool savefp16" [false]
PixelFilter "box" "float xradius" [.5] "float yradius" [.5]
Sampler "sobol" "integer pixelsamples" [{spp}] "integer seed" [73] "string randomization" "owen"
Integrator "path" "integer maxdepth" [2]
WorldBegin
LightSource "infinite" "rgb L" [1 1 1]
'''
def render(pbrt,imgtool,out,name,text):
 sp=out/f'{name}.pbrt';sp.write_text(text);run([pbrt,sp.name],out,out/f'{name}.render.log');run([imgtool,'convert',f'{name}.exr','--outfile',f'{name}.pfm'],out,out/f'{name}.convert.log');return read_pfm(out/f'{name}.pfm')
def rel(a,b):return abs(a-b)/max(abs(b),1e-30)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pbrt',type=Path,required=True);ap.add_argument('--imgtool',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--commit',default='unknown');a=ap.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);P=a.pbrt.resolve();I=a.imgtool.resolve();report={'schema':1,'commit':a.commit,'pbrt_sha256':sha(P),'imgtool_sha256':sha(I),'canaries':{},'failures':[]}
 try:
  e=render(P,I,out,'rimo_irradiance',scene('rimo_irradiance',True,[(0,0,1),(1,0,0),(0,1,0)],[0,0,0],512));vals=[lum(x) for x in e[2]]
  if max(rel(v,math.pi) for v in vals)>.012:raise Failure(f'irradiance/pi mismatch {vals}')
  report['canaries']['uniform_environment_irradiance']={'measured':vals,'expected':math.pi,'max_relative_error':max(rel(v,math.pi) for v in vals),'status':'pass'}
  c=render(P,I,out,'rimo_cone',scene('rimo_cone',False,[(0,0,1),(1,0,0),(0,1,0)],[.01,.2,.9],256));vals=[lum(x) for x in c[2]]
  if max(rel(v,1) for v in vals)>.008:raise Failure(f'cone/unit-radiance mismatch {vals}')
  if max(vals)-min(vals)>.008:raise Failure(f'cone/orientation invariance mismatch {vals}')
  report['canaries']['uniform_environment_cone_radiance']={'measured':vals,'expected':1.0,'max_relative_error':max(rel(v,1) for v in vals),'status':'pass'}
 except Failure as e: report['failures'].append(str(e))
 report['status']='pass' if not report['failures'] else 'fail';(out/'rimo-sensor-canaries.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps(report,indent=2));raise SystemExit(0 if report['status']=='pass' else 1)
if __name__=='__main__':main()
