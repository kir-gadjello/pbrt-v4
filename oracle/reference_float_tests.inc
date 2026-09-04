// Directed-rounding qualification for the PBRT_FLOAT_AS_DOUBLE path.
// Appended to src/pbrt/util/float_test.cpp by reference-oracle CI.

TEST(ReferenceOracleFloat, DirectedRoundingAtLargeScale) {
#ifdef PBRT_FLOAT_AS_DOUBLE
    const double inf = std::numeric_limits<double>::infinity();
    const double ninf = -inf;
    for (double a : {6371000.0, 1.0e9, 1.0e12, -6371000.0, -1.0e12}) {
        for (double b : {0.1, 1.0, 123.456789, -0.1, -123.456789}) {
            EXPECT_EQ(AddRoundUp(a, b), std::nextafter(a + b, inf));
            EXPECT_EQ(AddRoundDown(a, b), std::nextafter(a + b, ninf));
            EXPECT_EQ(SubRoundUp(a, b), std::nextafter(a - b, inf));
            EXPECT_EQ(SubRoundDown(a, b), std::nextafter(a - b, ninf));
            EXPECT_EQ(MulRoundUp(a, b), std::nextafter(a * b, inf));
            EXPECT_EQ(MulRoundDown(a, b), std::nextafter(a * b, ninf));
            if (b != 0) {
                EXPECT_EQ(DivRoundUp(a, b), std::nextafter(a / b, inf));
                EXPECT_EQ(DivRoundDown(a, b), std::nextafter(a / b, ninf));
            }
        }
    }
#else
    // PBRT vendors a pre-GTEST_SKIP GoogleTest. The f32 lane still executes
    // NextDoubleMatchesStandardLibrary below; large-scale directed rounding is
    // intentionally exercised only by the PBRT_FLOAT_AS_DOUBLE lane.
    return;
#endif
}

TEST(ReferenceOracleFloat, NextDoubleMatchesStandardLibrary) {
    for (double v : {0.0, -0.0, 1.0, -1.0, 6371000.0, 1.0e9, 1.0e12,
                     0x1p-500, -0x1p-500}) {
        EXPECT_EQ(NextFloatUp(v), std::nextafter(v, std::numeric_limits<double>::infinity()));
        EXPECT_EQ(NextFloatDown(v), std::nextafter(v, -std::numeric_limits<double>::infinity()));
    }
}
