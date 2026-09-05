#!/usr/bin/env python3
"""Compare a DUT irradiance PFM to a PBRT camera irradiance oracle buffer."""
from __future__ import annotations
import argparse,json,math,struct
from pathlib import Path
from typing import Tuple
RGB=Tuple[float,float,float]

def read_pfm(p:Path):
    with p.open('rb') as f:
        magic=f.readline().strip(); w,h=map(int,f.readline().split()); scale=float(f.readline()); ch=3 if magic==b'PF' else 1; raw=f.read(); vals=struct.unpack(('<' if scale<0 else '>')+f'{w*h*ch}f',raw)
    px=[(vals[i],vals[i+1],vals[i+2]) for i in range(0,len(vals),3)] if ch==3 else [(v,v,v) for v in vals]
    return w,h,px

def read_pgm(p:Path):
    with p.open('rb') as f:
        if f.readline().strip()!=b'P5': raise ValueError('expected P5')
        line=f.readline()
        while line.startswith(b'#'): line=f.readline()
        w,h=map(int,line.split()); mx=int(f.readline()); raw=f.read()
    return w,h,[v>0 for v in raw]

def lum(q):return .2126*q[0]+.7152*q[1]+.0722*q[2]
def qt(a,q):
    if not a: return 0.
    a=sorted(a); x=(len(a)-1)*q; i=int(x); j=min(i+1,len(a)-1); t=x-i
    return a[i]*(1-t)+a[j]*t

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--oracle',required=True,type=Path);ap.add_argument('--dut',required=True,type=Path);ap.add_argument('--mask',type=Path);ap.add_argument('--out',required=True,type=Path);ap.add_argument('--label',default='total');a=ap.parse_args()
    W,H,R=read_pfm(a.oracle); w,h,D=read_pfm(a.dut)
    if (W,H)!=(w,h):raise SystemExit('dimension mismatch')
    if a.mask:
        mw,mh,M=read_pgm(a.mask)
        if (mw,mh)!=(W,H):raise SystemExit('mask dimension mismatch')
    else:M=[True]*(W*H)
    pairs=[(r,d) for r,d,m in zip(R,D,M) if m and all(math.isfinite(x) for x in (*r,*d))]
    if not pairs: raise SystemExit('no finite comparison pixels')
    yr=[lum(r) for r,d in pairs]; yd=[lum(d) for r,d in pairs]
    p95=max(qt(yr,.95),1e-9); floor=max(1e-6,.01*p95); logeps=max(1e-7,.001*p95)
    rel=[abs(d-r)/max(abs(r),floor) for r,d in zip(yr,yd)]
    signed=[(d-r)/max(abs(r),floor) for r,d in zip(yr,yd)]
    loge=[abs(math.log2(max(d,0)+logeps)-math.log2(max(r,0)+logeps)) for r,d in zip(yr,yd)]
    rgbse=[[(d[k]-r[k])**2 for k in range(3)] for r,d in pairs]
    rmse=[math.sqrt(sum(v[k] for v in rgbse)/len(rgbse)) for k in range(3)]
    dark_thr=.02*p95; leak_thr=.05*p95
    dark=[i for i,r in enumerate(yr) if r<dark_thr]
    leak=sum(yd[i]>leak_thr and yd[i]>max(yr[i]*3,leak_thr) for i in dark)/max(len(dark),1)
    report={'schema':1,'label':a.label,'resolution':[W,H],'count':len(pairs),'reference_p95_luminance':p95,'relative_floor':floor,
      'rgb_rmse':rmse,'luminance_mae':sum(abs(d-r) for r,d in zip(yr,yd))/len(pairs),
      'relative_luminance_error':{f'p{p}':qt(rel,p/100) for p in (50,90,95,99)},
      'log2_luminance_error':{f'p{p}':qt(loge,p/100) for p in (50,90,95,99)},
      'under_20pct_fraction':sum(s<-.2 for s in signed)/len(signed),'over_20pct_fraction':sum(s>.2 for s in signed)/len(signed),
      'dark_region_fraction':len(dark)/len(pairs),'light_leak_false_positive_fraction_dark':leak}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2,sort_keys=True));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
