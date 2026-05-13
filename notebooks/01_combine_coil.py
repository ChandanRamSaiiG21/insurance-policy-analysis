import pandas as pd
import os

RAW = r"D:\DataAnalyticsProjects\insurance-policy-analysis\data\raw"

cols = [
    "MOSTYPE", "MAANTHUUR", "MGEMOMV", "MGODPR", "MRELGE", "MRELSA",
    "MRELOV", "MFALLEEN", "MFGEKIND", "MFWEKIND", "MOPLHOOG", "MOPLMIDD",
    "MOPLLAAG", "MBERHOOG", "MBERZELF", "MBERBOER", "MBERMIDD", "MBERARBG",
    "MBERARBO", "MSKA", "MSKB1", "MSKB2", "MSKC", "MSKD", "MHHUUR",
    "MHKOOP", "MAUT1", "MAUT2", "MAUT3", "MZFONDS", "MZPART", "MINKM30",
    "MINK3045", "MINK4575", "MINK7512", "MINK123M", "MINKGEM", "MKOOPKLA",
    "PWAPART", "PWABEDR", "PWALAND", "PPERSAUT", "PBESAUT", "PMOTSCO",
    "PVRAAUT", "PAANHANG", "PTRACTOR", "PWERKT", "PBROM", "PLEVEN",
    "PPERSONG", "PGEZONG", "PWAOREG", "PBRAND", "PZEILPL", "PPLEZIER",
    "PFIETS", "PINBOED", "PBYSTAND", "AWAPART", "AWABEDR", "AWALAND",
    "APERSAUT", "ABESAUT", "AMOTSCO", "AVRAAUT", "AANAHANG", "ATRACTOR",
    "AWERKT", "ABROM", "ALEVEN", "APERSONG", "AGEZONG", "AWAOREG",
    "ABRAND", "AZEILPL", "APLEZIER", "AFIETS", "AINBOED", "ABYSTAND",
    "CARAVAN"
]

train = pd.read_csv(os.path.join(RAW, "ticdata2000.txt"), sep="\t", header=None, names=cols)
test  = pd.read_csv(os.path.join(RAW, "ticeval2000.txt"), sep="\t", header=None, names=cols[:-1])
tgts  = pd.read_csv(os.path.join(RAW, "tictgts2000.txt"), sep="\t", header=None, names=["CARAVAN"])

test["CARAVAN"] = tgts["CARAVAN"].values
combined = pd.concat([train, test], ignore_index=True)

print(f"Train:    {train.shape}")
print(f"Test:     {test.shape}")
print(f"Combined: {combined.shape}")
print(f"Nulls:    {combined.isnull().sum().sum()}")
print(f"CARAVAN:\n{combined['CARAVAN'].value_counts()}")

combined.to_csv(os.path.join(RAW, "coil2000_combined.csv"), index=False)
print("\nSaved → coil2000_combined.csv")