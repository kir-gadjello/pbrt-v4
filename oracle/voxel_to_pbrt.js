#!/usr/bin/env node
'use strict';
const fs=require('fs'), path=require('path'), crypto=require('crypto');

function arg(name, def=null){ const i=process.argv.indexOf(name); return i>=0?process.argv[i+1]:def; }
const levelFile=arg('--level'); const outDir=path.resolve(arg('--out','oracle-voxel-out'));
const spp=Number(arg('--spp','32')); const xres=Number(arg('--xres','512')); const yres=Number(arg('--yres','288'));
const voxelOverride=Number(arg('--voxel-meters','0.1'));
if(!levelFile) throw new Error('--level required'); fs.mkdirSync(outDir,{recursive:true});

function shaFile(p){return crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');}
function clamp(x,a=0,b=1){return Math.min(b,Math.max(a,x));}
function loadLevel(file){
 const m=require(path.resolve(file)); let L;
 if(m.generateIslandRavine) L=m.generateIslandRavine();
 else if(m.generateBunkersUnderCanopy) L=m.generateBunkersUnderCanopy({voxelSizeM:voxelOverride});
 else if(m.generateHoledBuildingAmongTrees) L=m.generateHoledBuildingAmongTrees({voxelMeters:voxelOverride,resolutionStrategy:'reference-resample'});
 else if(m.buildBuildingCarcassAmongTrees) L=m.buildBuildingCarcassAmongTrees({voxelMeters:voxelOverride,resolutionPolicy:'reference-resample'});
 else throw new Error('unsupported level module '+file);
 const vol=L.volume||null;
 const dims=L.dimensions?[L.dimensions.x,L.dimensions.y,L.dimensions.z]:vol?[vol.dimX,vol.dimY,vol.dimZ]:[L.sizeX,L.sizeY,L.sizeZ];
 const vm=L.voxelSizeMeters??L.voxelSizeM??L.profile?.voxelMeters??voxelOverride;
 const table=L.materials||L.materialTable||m.MATERIAL_TABLE;
 let get, dense=null;
 // Oracle extraction is read-mostly and makes O(surface sweep) random reads.
 // Paged/deduplicated volumes are excellent engine storage but their getVoxel()
 // indirection is needlessly expensive here. Materialize one authoritative dense
 // snapshot when the generator exposes toDense(); this also freezes the world for
 // deterministic provenance and makes subsequent meshing linear-memory-bandwidth work.
 if(vol && typeof vol.toDense==='function') {
   dense=vol.toDense(); const [X,Y,Z]=dims;
   get=(x,y,z)=>(x<0||y<0||z<0||x>=X||y>=Y||z>=Z)?0:dense[x+X*(y+Y*z)]|0;
 } else if(vol && typeof vol.getVoxel==='function') get=(x,y,z)=>vol.getVoxel(x,y,z)|0;
 else if(L.voxels){ const [X,Y,Z]=dims, a=L.voxels; dense=a; get=(x,y,z)=>(x<0||y<0||z<0||x>=X||y>=Y||z>=Z)?0:a[x+X*(y+Y*z)]|0; }
 else throw new Error('no supported voxel storage');
 return {module:m,level:L,dims,vm,table,get,dense};
}
function familyColor(f,v){
 const t=(v&31)/31;
 const palettes={
  1:[[.30,.31,.31],[.58,.56,.50]],2:[[.20,.12,.06],[.45,.30,.14]],3:[[.08,.26,.045],[.24,.52,.08]],
  4:[[.20,.075,.025],[.52,.32,.15]],5:[[.04,.19,.025],[.24,.48,.07]],6:[[.38,.07,.015],[.78,.33,.025]],
  7:[[.20,.22,.25],[.72,.68,.58]]};
 const p=palettes[f]||[[.35,.35,.35],[.6,.6,.6]]; return p[0].map((a,i)=>a+(p[1][i]-a)*t);
}
function matInfo(desc,id){ desc=desc||{}; const name=(desc.name||desc.description||desc.familyName||('mat'+id)).toLowerCase();
 let base=desc.base||desc.baseRGB||desc.albedoHint||null; if(!base){const f=desc.family??(id>>5),v=desc.variant??(id&31); base=familyColor(f,v);}
 base=base.map(v=>clamp(Number(v)||0,0,.98)); const rough=clamp(desc.roughness??((desc.family===5||desc.family===6)?.62:.82),0,1);
 const metallic=clamp(desc.metallic??0,0,1); let emission=Array.isArray(desc.emission)?desc.emission.slice(0,3):[0,0,0];
 if((desc.emissionStrength||0)>0 && desc.emissionColor) emission=desc.emissionColor.map(v=>v*desc.emissionStrength);
 const transparent=/water|glass/.test(name) || (desc.transmission??0)>.72;
 const kind=/water/.test(name)?'water':/glass/.test(name)?'glass':transparent?'dielectric':metallic>=.55?'conductor':'diffuse';
 return {id,name,base,roughness:rough,alpha:rough*rough,metallic,emission,kind,eta:kind==='water'?1.333:1.5}; }

const W=loadLevel(levelFile), [X,Y,Z]=W.dims;
const infos=Array.from({length:256},(_,i)=>matInfo(W.table?.[i],i));
function trans(id){return id!==0 && (infos[id]?.kind==='water'||infos[id]?.kind==='glass'||infos[id]?.kind==='dielectric');}
function sample(x,y,z){return (x<0||y<0||z<0||x>=X||y>=Y||z>=Z)?0:W.get(x,y,z);}
function boundary(a,b,stats){
 if(a===b) return 0;
 if(a && !b) return a;
 if(!a && b) return -b;
 if(a&&b){
   const ta=trans(a), tb=trans(b);
   if(ta && !tb) return -b; // visible opaque wall through medium
   if(!ta && tb) return a;
   if(ta && tb && a!==b){stats.ambiguousTransmissive++; return 0;}
 }
 return 0;
}

const stats={rawBoundaryCells:0,greedyQuads:0,ambiguousTransmissive:0,byMaterial:{},byAxisSign:{}};
const quads=new Map(); function pushQuad(mat,q){ if(!quads.has(mat))quads.set(mat,[]); quads.get(mat).push(q); stats.greedyQuads++; stats.byMaterial[mat]=(stats.byMaterial[mat]||0)+1; }
const dims=[X,Y,Z];
for(let d=0;d<3;d++){
 const u=(d+1)%3, v=(d+2)%3, U=dims[u], V=dims[v]; const mask=new Int16Array(U*V);
 for(let s=-1;s<dims[d];s++){
   let n=0;
   for(let j=0;j<V;j++) for(let i=0;i<U;i++){
     const a=[0,0,0], b=[0,0,0]; a[d]=s;b[d]=s+1;a[u]=b[u]=i;a[v]=b[v]=j;
     const lab=boundary(sample(...a),sample(...b),stats); mask[n++]=lab; if(lab)stats.rawBoundaryCells++;
   }
   for(let j=0;j<V;j++) for(let i=0;i<U;){
     const idx=i+U*j, lab=mask[idx]; if(!lab){i++;continue;}
     let w=1; while(i+w<U && mask[idx+w]===lab)w++;
     let h=1; outer:for(;j+h<V;h++) for(let k=0;k<w;k++) if(mask[i+k+U*(j+h)]!==lab)break outer;
     for(let yy=0;yy<h;yy++)for(let xx=0;xx<w;xx++)mask[i+xx+U*(j+yy)]=0;
     const sign=lab>0?1:-1, mat=Math.abs(lab); pushQuad(mat,[d,sign,s+1,i,j,w,h]);
     const k=`${d}${sign>0?'+':'-'}`; stats.byAxisSign[k]=(stats.byAxisSign[k]||0)+1; i+=w;
   }
 }
}

const shift=[-X*W.vm/2,0,-Z*W.vm/2];
function qverts(q){ const [d,sign,plane,i,j,w,h]=q,u=(d+1)%3,v=(d+2)%3; const p=[0,0,0],du=[0,0,0],dv=[0,0,0]; p[d]=plane;p[u]=i;p[v]=j;du[u]=w;dv[v]=h;
 const arr=[p,[p[0]+du[0],p[1]+du[1],p[2]+du[2]],[p[0]+du[0]+dv[0],p[1]+du[1]+dv[1],p[2]+du[2]+dv[2]],[p[0]+dv[0],p[1]+dv[1],p[2]+dv[2]]];
 return arr.map(a=>a.map((x,k)=>x*W.vm+shift[k])); }
function writePly(mat,qs){ const nV=qs.length*4,nF=qs.length*2; const name=`mesh_mat_${String(mat).padStart(3,'0')}.ply`, p=path.join(outDir,name);
 const header=Buffer.from(`ply\nformat binary_little_endian 1.0\ncomment generated by voxel_to_pbrt.js\nelement vertex ${nV}\nproperty float x\nproperty float y\nproperty float z\nelement face ${nF}\nproperty list uchar uint vertex_indices\nend_header\n`);
 const vb=Buffer.allocUnsafe(nV*12); let vo=0; for(const q of qs)for(const v of qverts(q))for(const x of v){vb.writeFloatLE(x,vo);vo+=4;}
 const fb=Buffer.allocUnsafe(nF*13); let fo=0,base=0; for(const q of qs){const sign=q[1]; const tris=sign>0?[[0,1,2],[0,2,3]]:[[0,3,2],[0,2,1]]; for(const t of tris){fb.writeUInt8(3,fo++);for(const k of t){fb.writeUInt32LE(base+k,fo);fo+=4;}}base+=4;}
 fs.writeFileSync(p,Buffer.concat([header,vb,fb])); return {name,sha256:shaFile(p),quads:qs.length,triangles:nF,vertices:nV,bytes:fs.statSync(p).size}; }
const meshes={}; for(const [mat,qs] of [...quads.entries()].sort((a,b)=>a[0]-b[0]))meshes[mat]=writePly(mat,qs);

// occupancy bounds for camera placement
let min=[X,Y,Z],max=[-1,-1,-1],solid=0; for(let z=0;z<Z;z++)for(let y=0;y<Y;y++)for(let x=0;x<X;x++){if(sample(x,y,z){solid++;if(x<min[0])min[0]=x;if(y<min[1])min[1]=y;if(z<min[2])min[2]=z;if(x>max[0])max[0]=x;if(y>max[1])max[1]=y;if(z>max[2])max[2]=z;}}
function phys(v){return [v[0]*W.vm+shift[0],v[1]*W.vm+shift[1],v[2]*W.vm+shift[2]];}
const bmin=phys(min),bmax=phys([max[0]+1,max[1]+1,max[2]+1]); const ctr=bmin.map((x,i)=>(x+buax[i])/2), ext=bmin.map((x,i)=>bmax[i]-x); const R=Math.max(...ext);
const target=[ctr[0],bmin[1]+ext[1]*.42,ctr[2]];
const cameras=[
 {name:'a',eeye:[ctr[0]+.72*R,bmin[1]+.58*R,ctr[2]-.92*R],target,fov:43},
 {name:'b',eye:[ctr[0]-.88*R,bmin[1]+.40*R,ctr[2]+.72*R],target:[ctr[0],bmin[1]+ext[1]*.34,ctr[2]],fov:48}
];
function fmt3(a){return a.map(v=>Number(v).toFixed(6)).join(' ')}
function matDecl(info){const key=`mat_${info.id}`; if(info.kind==='conductor')return `MakeNamedMaterial "${key}" "string type" "conductor" "rgb reflectance" [${fmt3(info.base)}] "float roughness" [${info.alpha}] "bool remaproughness" [false]\n`;
 if(info.kind==='water'||info.kind==='glass'||info.kind==='dielectric')return `MakeNamedMaterial "${key}" "string type" "dielectric" "float eta" [${info.eta}] "float roughness" [${info.alpha}] "bool remaproughness" [false]\n`;
 return `MakeNamedMaterial "${key}" "string type" "diffuse" "rgb reflectance" [${fmt3(info.base)}]\n`;}
let anyWater=[...quads.keys()].some(id=>infos[id].kind==='water');
for(const cam of cameras){ let s=`LookAt ${fmt3(cam.eye)}\n       ${fmt3(cam.target)}\n       0 1 0\nCamera "perspective" "float fov" [${cam.fov}]\nFilm "rgb" "integer xresolution" [${xres}] "integer yresolution" [${yres}] "string filename" ["${path.basename(levelFile,'.js')}_${cam.name}_${spp}spp.exr"]\nSampler "halton" "integer pixelsamples" [${spp}]\nIntegrator "volpath" "integer maxdepth" [10]\n`;
 if(anyWater) s+=`MakeNamedMedium "water_medium" "string type" "homogeneous" "rgb sigma_a" [0.18 0.07 0.025] "rgb sigma_s" [0.008 0.012 0.016]\n`;
 s+='WorldBegin\nLightSource "infinite" "rgb L" [0.12 0.16 0.25]\nLightSource "distant" "rgb L" [2.4 2.1 1.65] "point3 from" [-30 45 -50] "point3 to" [0 0 0]\n';
 for(const id of [...quads.keys()].sort((a,b)=>a-b))s+=matDecl(infos[id]);
 for(const id of [...quads.keys()].sort((a,b)=>a-b)){const info=infos[id],mesh=meshes[id]; s+='AttributeBegin\n'; s+=`NamedMaterial "mat_${id}"\n`; if(info.kind==='water')s+='MediumInterface "water_medium" ""\n'; if(info.emission.some(v=>v>0))s+=`AreaLightSource "diffuse" "rgb L" [${fmt3(info.emission)}]\n`; s+=`Shape "plymesh" "string filename" ["${mesh.name}"]\nAttributeEnd\n`;}
 const p=path.join(outDir,`${path.basename(levelFile,'.js')}_${cam.name}.pbrt`);fs.writeFileSync(p,s);cam.scene=path.basename(p);cam.sha256=shaFile(p);
}
const manifest={schema:1,source:path.basename(levelFile),sourceSha256:shaFile(path.resolve(levelFile)),fingerprint64:W.level.fingerprint64||null,dims:W.dims,voxelMeters:W.vm,solidVoxels:solid,boundsMeters:{min:bmin,max:bmax},meshStats:stats,meshes,materials:Object.fromEntries([...quads.keys()].sort((a,b)=>a-b).map(id=>[id,infos[id]])),cameras,adapterSha256:shaFile(__filename)};
fs.writeFileSync(path.join(outDir,'manifest.json'),JSON.stringify(manifest,null,2)); console.log(JSON.stringify({dims:W.dims,vm:W.vm,solid,quads:stats.greedyQuads,triangles:stats.greedyQuads*2,materials:quads.size,ambiguous:stats.ambiguousTransmissive,cameras},null,2));
