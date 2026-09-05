#!/usr/bin/env bash
set -euo pipefail
PBRT=${PBRT:?set PBRT}; IMGTOOL=${IMGTOOL:?set IMGTOOL}; SCENE_DIR=${SCENE_DIR:?set SCENE_DIR}; OUT=${OUT:?set OUT}
SPP=${SPP:-128}; SEEDS=${SEEDS:-11,29}; NTHREADS=${NTHREADS:-4}; PROCESSOR=${PROCESSOR:-$(dirname "$0")/process_irradiance_camera.py}; PROBE_STRIDE=${PROBE_STRIDE:-4}
mkdir -p "$OUT"
mapfile -t ROWS < <(python - "$SCENE_DIR/manifest.json" <<'PY'
import json,sys
m=json.load(open(sys.argv[1]))
for c in m['cameras']:
 print('\t'.join([c['name'],c['totalScene'],c['directScene'],json.dumps(c,separators=(',',':'))]))
PY
)
IFS=',' read -ra SEEDV <<< "$SEEDS"
for row in "${ROWS[@]}"; do
 IFS=$'\t' read -r NAME TOTAL DIRECT CAMJSON <<< "$row"
 if [[ -n "${CAMERAS:-}" && ",${CAMERAS}," != *",${NAME},"* ]]; then continue; fi
 CD="$OUT/$NAME"; mkdir -p "$CD"; printf '%s' "$CAMJSON" > "$CD/camera.json"
 # deterministic center-ray geometry alignment
 "$PBRT" --quiet --nthreads "$NTHREADS" --spp 1 --disable-pixel-jitter --outfile "$CD/alignment.exr" "$SCENE_DIR/$TOTAL"
 TOTAL_ARGS=(); DIRECT_ARGS=(); IDX=0
 for SEED in "${SEEDV[@]}"; do
   ST="$SCENE_DIR/.oracle_${NAME}_total_seed${SEED}.pbrt"; SD="$SCENE_DIR/.oracle_${NAME}_direct_seed${SEED}.pbrt"
   python - "$SCENE_DIR/$TOTAL" "$ST" "$SEED" <<'PY'
import re,sys
s=open(sys.argv[1]).read();s,n=re.subn(r'(Sampler\s+"halton"[^\n]*"integer seed"\s*\[)\s*[-+]?\d+(\])',rf'\g<1>{sys.argv[3]}\2',s,count=1);assert n==1;open(sys.argv[2],'w').write(s)
PY
   python - "$SCENE_DIR/$DIRECT" "$SD" "$SEED" <<'PY'
import re,sys
s=open(sys.argv[1]).read();s,n=re.subn(r'(Sampler\s+"halton"[^\n]*"integer seed"\s*\[)\s*[-+]?\d+(\])',rf'\g<1>{sys.argv[3]}\2',s,count=1);assert n==1;open(sys.argv[2],'w').write(s)
PY
   TE="$CD/total_seed${SEED}.exr"; DE="$CD/direct_seed${SEED}.exr"
   "$PBRT" --quiet --nthreads "$NTHREADS" --spp "$SPP" --outfile "$TE" "$ST"
   "$PBRT" --quiet --nthreads "$NTHREADS" --spp "$SPP" --outfile "$DE" "$SD"
   rm -f "$ST" "$SD"; TOTAL_ARGS+=(--total "$TE"); DIRECT_ARGS+=(--direct "$DE"); IDX=$((IDX+1))
 done
 python "$PROCESSOR" --imgtool "$IMGTOOL" --alignment "$CD/alignment.exr" "${TOTAL_ARGS[@]}" "${DIRECT_ARGS[@]}" --out "$CD" --camera-json "$CD/camera.json" --probe-stride "$PROBE_STRIDE" --spp "$SPP" --seeds "$SEEDS"
done
python - "$SCENE_DIR/manifest.json" "$OUT" "$SPP" "$SEEDS" <<'PY'
import json,sys,glob,os,hashlib
mf,out,spp,seeds=sys.argv[1:];m=json.load(open(mf));cams=[json.load(open(p)) for p in sorted(glob.glob(out+'/*/camera-report.json'))]
h=lambda p:hashlib.sha256(open(p,'rb').read()).hexdigest()
r={'schema':2,'source':m['source'],'sourceSha256':m['sourceSha256'],'adapterSha256':m['adapterSha256'],'definition':m['irradianceScope']['definition'],'scope':m['irradianceScope'],'lighting':m['lighting'],'spp_per_seed':int(spp),'seeds':[int(x) for x in seeds.split(',')],'effective_spp':int(spp)*len(seeds.split(',')),'camera_count':len(cams),'comparison_pixels':sum(c['comparison_pixels'] for c in cams),'mean_indirect_energy_fraction':sum(c['indirect_energy_fraction_significant'] for c in cams)/len(cams),'mean_seed_disagreement_p95':sum(c['seed_disagreement_total']['p95'] for c in cams if c['seed_disagreement_total'])/max(1,sum(c['seed_disagreement_total'] is not None for c in cams)),'manifestSha256':h(mf),'cameras':cams}
open(out+'/suite-report.json','w').write(json.dumps(r,indent=2,sort_keys=True));print(json.dumps({k:r[k] for k in ('source','effective_spp','camera_count','comparison_pixels','mean_indirect_energy_fraction','mean_seed_disagreement_p95')},indent=2))
PY
