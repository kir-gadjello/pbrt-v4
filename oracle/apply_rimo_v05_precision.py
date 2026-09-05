#!/usr/bin/env python3
"""Narrow corrections demonstrated by the RIMO f64 diagnostic run.
No acceptance tolerances are widened. AtomicFloat remains explicitly binary32.
EXR matrix metadata remains binary32 by its existing interchange contract.
"""
from pathlib import Path

def once(s,a,b):
 if s.count(a)!=1:raise RuntimeError(f'Unexpected seam ({s.count(a)}): {a[:80]}')
 return s.replace(a,b,1)
# Actual defect: a float atomic cannot use renderer-dependent FloatBits. In f64,
# construction writes f32 bits while Add subsequently interprets them as f64.
p=Path('src/pbrt/util/parallel.h');s=p.read_text();a=s.index('class AtomicFloat {');b=s.index('class AtomicDouble {',a)
part=s[a:b].replace('FloatBits','uint32_t');s=s[:a]+part+s[b:];p.write_text(s)
p=Path('src/pbrt/util/float_test.cpp');s=p.read_text();a=s.index('TEST(FloatingPoint, AtomicFloat)');b=s.index('TEST(Half, Basics)',a)
part=s[a:b].replace('Float f = 0.;','float f = 0.f;').replace('1.0251','1.0251f')
part=part.replace('    AtomicFloat af(0);','    AtomicFloat initialized(1.25f);\n    EXPECT_EQ(float(initialized), 1.25f);\n    initialized = 2.5f;\n    EXPECT_EQ(float(initialized), 2.5f);\n    initialized.Add(.125f);\n    EXPECT_EQ(float(initialized), 2.625f);\n    AtomicFloat af(0);')
s=s[:a]+part+s[b:];p.write_text(s)
# Test-reference narrowing, not transport interval arithmetic.
p=Path('src/pbrt/util/math_test.cpp');s=p.read_text();n=s.count('float preciseResult =');assert n==5,n
s=s.replace('float preciseResult =','double preciseResult =');p.write_text(s)
# Parser reads the requested renderer precision, unlike the old f32 literal.
p=Path('src/pbrt/util/file_test.cpp');s=p.read_text();s=once(s,'{1.f, -2.5f, 300.f, -.475f, 5.25f, 6.f}','{Float(1), Float(-2.5), Float(300), Float(-.475), Float(5.25), Float(6)}');p.write_text(s)
# Existing EXR camera transforms are M44f: compare to the exact stored precision.
p=Path('src/pbrt/util/image_test.cpp');s=p.read_text()
for field,expected in [('cameraFromWorld','w2c'),('NDCFromWorld','w2n')]:
 s=once(s,f'    EXPECT_EQ(*inMetadata.{field}, {expected});',f'    for (int i=0;i<4;++i) for (int j=0;j<4;++j)\n        EXPECT_EQ((*inMetadata.{field})[i][j], Float(float({expected}[i][j])));')
p.write_text(s)
# Retain strict 5% frequency bounds and all PMF identities, increase deterministic
# sample budget instead of changing seeds or relaxing the failing bound.
p=Path('src/pbrt/lightsamplers_test.cpp');s=p.read_text();a=s.index('TEST(BVHLightSampling, PointVaryPower)');b=s.index('\nTEST(',a+1)
s=s[:a]+s[a:b].replace('nSamples = 100000;','nSamples = 1000000;')+s[b:];p.write_text(s)
# Match PBRT's unit-white light and default calibrated film exactly; unlike raw
# SampledSpectrum(1), this has neutral unit RGB expectation. No image-ratio estimate.
p=Path('src/pbrt/cpu/integrators.cpp');s=p.read_text()
s=once(s,'#include <algorithm>','#include <algorithm>\n#include <pbrt/util/colorspace.h>')
s=once(s,'    Interaction p0(input.o, input.time, input.medium);','    SampledSpectrum white = RGBIlluminantSpectrum(*RGBColorSpace::sRGB, RGB(1,1,1)).Sample(lambda) / SpectrumToPhotometric(&RGBColorSpace::sRGB->illuminant);\n    Interaction p0(input.o, input.time, input.medium);')
s=once(s,'if (LengthSquared(ray.d)==0) return SampledSpectrum(1);','if (LengthSquared(ray.d)==0) return white;')
s=once(s,'        if (!hit) return tr;','        if (!hit) return tr * white;');p.write_text(s)
print('RIMO precision and transmittance color-contract corrections installed')
