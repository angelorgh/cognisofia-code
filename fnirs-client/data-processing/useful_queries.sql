-- seleccionar datos con los pares de emisores y detectores adyacentes
select 
ts,
time_elapsed,
stimulus,
-- S4 D12
s4_d12_740nm_rp,
s4_d12_740nm_lp,
s4_d12_850nm_rp,
s4_d12_850nm_lp,
-- S4 D11
s4_d11_740nm_rp,
s4_d11_740nm_lp,
s4_d11_850nm_rp,
s4_d11_850nm_lp,
-- S5 D12
s5_d12_740nm_rp,
s5_d12_740nm_lp,
s5_d12_850nm_rp,
s5_d12_850nm_lp,
-- S5 D13
s5_d13_740nm_rp,
s5_d13_740nm_lp,
s5_d13_850nm_rp,
s5_d13_850nm_lp,
-- S5 D15
s5_d15_740nm_rp,
s5_d15_740nm_lp,
s5_d15_850nm_rp,
s5_d15_850nm_lp,
-- S1 D11
s1_d11_740nm_rp,
s1_d11_740nm_lp,
s1_d11_850nm_rp,
s1_d11_850nm_lp,
-- S1 D9
s1_d9_740nm_rp,
s1_d9_740nm_lp,
s1_d9_850nm_rp,
s1_d9_850nm_lp,
-- S3 D12
s3_d12_740nm_rp,
s3_d12_740nm_lp,
s3_d12_850nm_rp,
s3_d12_850nm_lp,
-- S3 D11
s3_d11_740nm_rp,
s3_d11_740nm_lp,
s3_d11_850nm_rp,
s3_d11_850nm_lp,
-- S3 D10
s3_d10_740nm_rp,
s3_d10_740nm_lp,
s3_d10_850nm_rp,
s3_d10_850nm_lp,
-- S3 D15
s3_d15_740nm_rp,
s3_d15_740nm_lp,
s3_d15_850nm_rp,
s3_d15_850nm_lp,
-- S7 D13
s7_d13_740nm_rp,
s7_d13_740nm_lp,
s7_d13_850nm_rp,
s7_d13_850nm_lp,
-- S7 D15
s7_d15_740nm_rp,
s7_d15_740nm_lp,
s7_d15_850nm_rp,
s7_d15_850nm_lp,
-- S7 D16
s7_d16_740nm_rp,
s7_d16_740nm_lp,
s7_d16_850nm_rp,
s7_d16_850nm_lp,
-- S7 D14
s7_d14_740nm_rp,
s7_d14_740nm_lp,
s7_d14_850nm_rp,
s7_d14_850nm_lp,
-- S2 D9
s2_d9_740nm_rp,
s2_d9_740nm_lp,
s2_d9_850nm_rp,
s2_d9_850nm_lp,
-- S2 D11
s2_d11_740nm_rp,
s2_d11_740nm_lp,
s2_d11_850nm_rp,
s2_d11_850nm_lp,
-- S2 D10
s2_d10_740nm_rp,
s2_d10_740nm_lp,
s2_d10_850nm_rp,
s2_d10_850nm_lp,
-- S6 D10
s6_d10_740nm_rp,
s6_d10_740nm_lp,
s6_d10_850nm_rp,
s6_d10_850nm_lp,
-- S6 D15
s6_d15_740nm_rp,
s6_d15_740nm_lp,
s6_d15_850nm_rp,
s6_d15_850nm_lp,
-- S6 D14
s6_d14_740nm_rp,
s6_d14_740nm_lp,
s6_d14_850nm_rp,
s6_d14_850nm_lp,
-- S8 D16
s8_d16_740nm_rp,
s8_d16_740nm_lp,
s8_d16_850nm_rp,
s8_d16_850nm_lp,
-- S8 D14
s8_d14_740nm_rp,
s8_d14_740nm_lp,
s8_d14_850nm_rp,
s8_d14_850nm_lp
from frames where session_id = 'bbb7e4c5-46cd-42f5-8e66-1e303f30a01a'