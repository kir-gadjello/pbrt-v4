#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,math,struct,subprocess
from pathlib import Path

def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def run(cmd,cwd,log):
 with log.open('w') as o:
  p=subprocess.run(list(map(str,cmd)),cwd=cwd,stdout=o,stderr=subprocess.STDOUT)
 if p.returncode:raise SystemExit(f'command failed: {cmd}; see {log}')
def read_pfm(p):
 with p.open('rb') as f:
  magic=f.readline().strip();w,h=map(int,f.readline().split());scale=float(f.readline());ch=3 if magic==b'PF' else 1;v=struct.unpack(('<' if scale<0 else '>')+f'{w*h*ch}f',f.read())
 px=[(v[i],v[i+1],v[i+2]) for i in range(0,len(v),3)] if ch==3 else [(x,x,x) for x in v]
 return w,h,px
def write_pfm(p,w,h,px):
 a=[]
 for q in px:a.extend(q)
 with p.open('wb') as f:f.write(f'PF\n{w} {h}\n-1.000000\n'.encode());f.write(struct.pack('<'+f'{len(a)}f',*a))
def write_pgm(p,w,h,m):
 with p.open('wb') as f:f.write(f'P5\n{w} {h}\n255\n'.encode());f.write(bytes(255 if x else 0 for x in m))
def lum(q):return .2126*q[0]+.7152*q[1]+.0722*q[2]
def qt(v,q):
 if not v:return 0.
 a=sorted(v);x=(len(a)-1)*q;i=int(x);j=min(i+1,len(a)-1);t=x-i;return a[i]*(1-t)+a[j]*t
def stats(px,m):
 s=[p for p,k in zip(px,m) if k];f=[x for p in s for x in p];y=[lum(p) for p in s]
 return {'count':len(s),'min_channel':min(f),'max_channel':max(f),'mean_luminance':sum(y)/len(y),'luminance_p01':qt(y,.01),'luminance_p05':qt(y,.05),'luminance_p50':qt(y,.5),'luminance_p95':qt(y,.95),'luminance_p99':qt(y,.99),'negative_luminance_fraction':sum(x<0 for x in y)/len(y)}
def avg(imgs):
 w,h=imgs[0][:2];n=len(imgs);return w,h,[tuple(sum(im[2][i][k] for im in imgs)/n for k in range(3)) for i in range(w*h)]
def disagree(A,B,m):
 a=[lum(x) for x,k in zip(A,m) if k];b=[lum(x) for x,k in zip(B,m) if k];mid=[(x+y)/2 for x,y in zip(a,b)];p95=max(qt(mid,.95),1e-9);floor=.01*p95;r=[abs(x-y)/max(abs((x+y)/2),floor) for x,y in zip(a,b)];sig=[i for i,v in enumerate(mid) if v>=.05*p95];rs=[abs(a[i]-b[i])/max(abs(mid[i]),floor) for i in sig];an=[abs(x-y)/p95 for x,y in zip(a,b)];return {'mean':sum(r)/len(r),'p50':qt(r,.5),'p90':qt(r,.9),'p95':qt(r,.95),'p99':qt(r,.99),'floor':floor,'significant_threshold':.05*p95,'significant_fraction':len(sig)/len(mid),'significant_p50':qt(rs,.5),'significant_p90':qt(rs,.9),'significant_p95':qt(rs,.95),'absdiff_over_p95_p95':qt(an,.95)}
def edge_mask(w,h,P,N,V,eye):
 def d(a,b):return math.sqrt(sum((a[k]-b[k])**2 for k in range(3)))
 O=[False]*(w*h)
 for y in range(1,h-1):
  for x in range(1,w-1):
   i=y*w+x
   if not V[i]:continue
   rr=max(.45,.06*d(P[i],eye));ok=True
   for j in (i-1,i+1,i-w,i+w):
    if not V[j] or sum(N[i][k]*N[j][k] for k in range(3))<.72 or d(P[i],P[j])>rr:ok=False;break
   O[i]=ok
 return O
ap=argparse.ArgumentParser();ap.add_argument('--imgtool',required=True,type=Path);ap.add_argument('--alignment',required=True,type=Path);ap.add_argument('--total',action='append',required=True,type=Path);ap.add_argument('--direct',action='append',required=True,type=Path);ap.add_argument('--out',required=True,type=Path);ap.add_argument('--camera-json',required=True,type=Path);ap.add_argument('--probe-stride',type=int,default=4);ap.add_argument('--spp',type=int,default=0);ap.add_argument('--seeds',default='');a=ap.parse_args();o=a.out;o.mkdir(parents=True,exist_ok=True);cam=json.loads(a.camera_json.read_text())
def ex(src,ch,dst):run([a.imgtool,'convert',src,'--channels',ch,'--outfile',dst.name],o,dst.with_suffix(dst.suffix+'.log'))
# Alignment
Pp,Np,Nsp=o/'position.pfm',o/'normal.pfm',o/'shading_normal.pfm';ex(a.alignment,'P.X,P.Y,P.Z',Pp);ex(a.alignment,'N.X,N.Y,N.Z',Np);ex(a.alignment,'Ns.X,Ns.Y,Ns.Z',Nsp);W,H,P=read_pfm(Pp);_,_,N=read_pfm(Np);_,_,Ns=read_pfm(Nsp);V=[all(math.isfinite(x) for x in (*p,*n)) and sum(q*q for q in n)>.25 for p,n in zip(P,N)];M=edge_mask(W,H,P,N,V,cam['eye']);write_pgm(o/'valid_mask.pgm',W,H,V);write_pgm(o/'comparison_mask.pgm',W,H,M)
Ts=[];Ds=[]
for idx,(t,d) in enumerate(zip(a.total,a.direct)):
 tp=o/f'total_seed{idx}.pfm';dp=o/f'direct_seed{idx}.pfm';ex(t,'R,G,B',tp);ex(d,'R,G,B',dp);Ts.append(read_pfm(tp));Ds.append(read_pfm(dp))
_,_,T=avg(Ts);_,_,D=avg(Ds);I=[tuple(t[k]-d[k] for k in range(3)) for t,d in zip(T,D)];write_pfm(o/'total.pfm',W,H,T);write_pfm(o/'direct.pfm',W,H,D);write_pfm(o/'indirect.pfm',W,H,I)
for name in ('total','direct','indirect'):run([a.imgtool,'convert',f'{name}.pfm','--outfile',f'{name}.png'],o,o/f'{name}.png.log')
with (o/'probes.jsonl').open('w') as f:
 for y in range(0,H,a.probe_stride):
  for x in range(0,W,a.probe_stride):
   i=y*W+x
   if M[i]:f.write(json.dumps({'x':x,'y':y,'P':P[i],'N':N[i],'Ns':Ns[i],'E_total':T[i],'E_direct':D[i],'E_indirect':I[i]},separators=(',',':'))+'\n')
stT,stD,stI=stats(T,M),stats(D,M),stats(I,M);yt=[lum(x) for x,k in zip(T,M) if k];yi=[lum(x) for x,k in zip(I,M) if k];p95=max(qt(yt,.95),1e-9);sel=[i for i,v in enumerate(yt) if v>.01*p95];ifr=sum(yi[i] for i in sel)/max(sum(yt[i] for i in sel),1e-30)
seedvals=[int(x) for x in a.seeds.split(',') if x.strip()];r={'schema':2,'spp_per_seed':a.spp,'seeds':seedvals,'effective_spp':a.spp*max(1,len(seedvals)),'camera':cam,'resolution':[W,H],'valid_pixels':sum(V),'comparison_pixels':sum(M),'comparison_fraction':sum(M)/(W*H),'total':stT,'direct':stD,'indirect':stI,'indirect_energy_fraction_significant':ifr,'seed_disagreement_total':disagree(Ts[0][2],Ts[1][2],M) if len(Ts)>1 else None,'seed_disagreement_direct':disagree(Ds[0][2],Ds[1][2],M) if len(Ds)>1 else None,'input_sha256':{'alignment':sha(a.alignment),'total':[sha(p) for p in a.total],'direct':[sha(p) for p in a.direct]},'artifact_sha256':{p.name:sha(p) for p in (Pp,Np,Nsp,o/'total.pfm',o/'direct.pfm',o/'indirect.pfm',o/'comparison_mask.pgm')}};(o/'camera-report.json').write_text(json.dumps(r,indent=2,sort_keys=True));print(json.dumps({'camera':cam['name'],'comparison_pixels':sum(M),'Emean':stT['mean_luminance'],'indirect_fraction':ifr,'seed_p95':r['seed_disagreement_total']['p95'] if r['seed_disagreement_total'] else None}))
