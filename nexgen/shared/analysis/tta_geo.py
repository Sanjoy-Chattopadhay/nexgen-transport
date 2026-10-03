"""Offline geocoding for Indian destinations (TTA dashboard geo map).

Exact city match first, then approximate centroid by the first two digits of
the destination pincode. No external geocoding API needed.
"""
import re

ORIGIN = {"name": "JAMSHEDPUR", "lat": 22.8046, "lon": 86.2029}

CITY_COORDS = {
    "TIRUNINRAVUR": (13.1147, 80.0300), "JAIPUR": (26.9124, 75.7873),
    "PATNA CITY": (25.5941, 85.1376), "PATNA": (25.5941, 85.1376),
    "PALWAL": (28.1447, 77.3260),
    "JAMSHEDPUR": (22.8046, 86.2029), "BANGALORE CITY": (12.9716, 77.5946),
    "BANGALORE": (12.9716, 77.5946), "BENGALURU": (12.9716, 77.5946),
    "PITHAMPUR (M.P.)": (22.6076, 75.6799), "BALLABGARH": (28.3399, 77.3222),
    "GAMHARIA": (22.8680, 86.0950), "KANHAN": (21.2422, 79.1858),
    "HOWRAH": (22.5958, 88.2636), "JAJPUR ROAD": (20.9517, 86.1200),
    "CHENNAI": (13.0827, 80.2707), "RUDRAPUR": (28.9845, 79.4141),
    "KANPUR": (26.4499, 80.3319), "FARIDABAD": (28.4089, 77.3178),
    "GOVINDPUR": (23.8360, 86.5200), "TANGI": (20.5200, 85.8300),
    "SANSWADI": (18.6800, 74.0500), "AMTA": (22.5833, 88.0167),
    "DANKUNI": (22.6800, 88.2900), "RANJANGAON": (18.7726, 74.2482),
    "JAMSHEDPUR (IBMD)": (22.7900, 86.1900), "RANIPPETTAI": (12.9249, 79.3308),
    "CHANDAULI MAJH": (25.2617, 83.2647), "GIRIDIH": (24.1913, 86.3096),
    "PURNEA JN.": (25.7771, 87.4753), "JCAPCPL_JSR WORKS": (22.7920, 86.2100),
    "ADITYAPUR": (22.7795, 86.1571), "CALCUTTA": (22.5726, 88.3639),
    "KOLKATA": (22.5726, 88.3639),
    "LUDHIANA": (30.9010, 75.8573), "NAGPUR": (21.1458, 79.0882),
    "RANIGANJ": (23.6100, 87.1300), "BHUBANESWAR": (20.2961, 85.8245),
    "MALDA TOWN": (25.0108, 88.1411), "GORAKHPUR CANTT": (26.7606, 83.3732),
    "MEDAK": (17.6000, 78.4000), "SANAND": (22.9922, 72.3819),
    "CHAKAN": (18.7606, 73.8636), "DELHI JN": (28.6139, 77.2090),
    "DELHI": (28.6139, 77.2090), "NEW DELHI": (28.6139, 77.2090),
    "GHAZIABAD": (28.6692, 77.4538), "BIDADI": (12.7975, 77.3838),
    "KHOPOLI": (18.7856, 73.3437), "HYDERABAD": (17.3850, 78.4867),
    "TALOJA": (19.0868, 73.1000), "BIDADI (TKML)": (12.8000, 77.3900),
    "TRYL-BARA": (22.8300, 86.2400), "AURANGABAD": (24.7520, 84.3742),  # Bihar
    "INDORE": (22.7196, 75.8577), "JAMURIA": (23.7047, 87.0783),
    "KOLAR": (13.1367, 78.1292), "HUBLI": (15.3647, 75.1240),
    "DHANBAD": (23.7957, 86.4304), "DUBURI": (20.9800, 86.0300),
    "RAJPURA JN": (30.4840, 76.5940), "TBL- BARA AGRICO": (22.8320, 86.2430),
    "SINGUR": (22.8100, 88.2300), "RANCHI": (23.3441, 85.3096),
    "GUMMIDIPUNDI": (13.4076, 80.1087), "CUTTACK": (20.4625, 85.8828),
    "VIRAMGAM": (23.1200, 72.0300), "JSR WORKS PLANTTRY": (22.8000, 86.2000),
    "MEDCHAL": (17.6297, 78.4809), "SAHIBABAD": (28.6832, 77.3811),
    "RAIPUR": (21.2514, 81.6296), "CHANGODAR": (22.9500, 72.4600),
    "DHOLERA": (22.2437, 72.1935), "SARAIKELA KHARSAWAN": (22.7000, 85.9300),
    "KALAMBOLI TSL": (19.0330, 73.1000), "ABHOYAPUR": (26.1445, 91.7362),
    "VISHAKHAPATNAM": (17.6868, 83.2185), "AHMEDABAD": (23.0225, 72.5714),
    "PUNE": (18.5204, 73.8567), "DARBHANGA": (26.1542, 85.8918),
    "AGRA CITY": (27.1767, 78.0081), "VITHALPUR, GUJRAT": (23.2800, 71.9200),
    "ROURKELA": (22.2604, 84.8536), "HARRAWALA": (30.2679, 78.0790),
    "DHENKANAL": (20.6593, 85.5980), "BHIWADI": (28.2103, 76.8606),
    "UDHAM SINGH NAGAR": (28.9750, 79.4000), "KARUR %SR#": (10.9601, 78.0766),
    "GAUHATI": (26.1445, 91.7362), "JULLUNDUR CITY": (31.3260, 75.5762),
    "RAMGARH CANTT": (23.6363, 85.5124), "GANNAVARAM": (16.5410, 80.8000),
    "JABALPUR": (23.1815, 79.9864), "SURAT": (21.1702, 72.8311),
    "BAREILLY": (28.3670, 79.4304), "ADRA": (23.4933, 86.6700),
    "HOOGHLY": (22.9089, 88.3967), "RAJKOT": (22.3039, 70.8022),
    "BARNI HAT": (26.0500, 91.8700), "BOKARO": (23.6693, 86.1511),
    "VADODARA": (22.3072, 73.1812), "ODHAV": (23.0300, 72.6700),
    "KANPUR CENTRAL(CGS)": (26.4530, 80.3500), "KAPURTHALA": (31.3800, 75.3800),
    "BERHAMPUR COURT": (24.0900, 88.2500), "PERAMBUR WORKS": (13.1187, 80.2338),
    "NORTH 24-PARGANAS": (22.7300, 88.3500), "BOKARO STEEL CY": (23.6690, 86.1510),
    "MADHU BHANI": (26.3550, 86.0710), "PANT NAGAR": (29.0222, 79.4908),
    "KOLAGHAT": (22.4300, 87.8700), "VARANASI": (25.3176, 82.9739),
    "BHARATPUR": (27.2173, 77.4901), "BHOPAL": (23.2599, 77.4126),
    "BERHAMPUR GANJAM": (19.3150, 84.7941), "DEHRADUN": (30.3165, 78.0322),
    "KHARAGPUR": (22.3460, 87.2320), "FALNA": (25.2300, 73.2400),
    "NANDED": (19.1383, 77.3210), "COCHIN HARBOUR TERMI": (9.9312, 76.2673),
    "KOLHAPUR": (16.7050, 74.2433), "SALEM JN": (11.6643, 78.1460),
    "MUGHAL SARAI": (25.2815, 83.1198), "HASSAN": (13.0068, 76.1004),
    "GAMHARIA-SANFAB": (22.8650, 86.0930), "FALTA": (22.3000, 88.1200),
    "CHANDIGARH": (30.7333, 76.7794), "ANDAL": (23.6000, 87.2000),
    "SANGAREDDY": (17.6248, 78.0866), "PENUKONDA": (14.0810, 77.5960),
    "COOCHBIHAR": (26.3452, 89.4482), "GORAKHPUR STORE": (26.7550, 83.3700),
    "COIMBATORE": (11.0168, 76.9558), "KOLABIRA": (22.8600, 86.1000),
    "BEGUNIA": (20.1900, 85.4200), "TATANAGAR": (22.7770, 86.1450),
    "HARIDWAR": (29.9457, 78.1642), "KANHE": (18.7400, 73.5800),
    "DALOD": (23.3000, 72.2000), "BHILAI": (21.1938, 81.3509),
    "BONDAMUNDA": (22.2100, 84.9200), "THANE": (19.2183, 72.9781),
    "PILKHUWA": (28.7100, 77.6500), "HOSUR": (12.7409, 77.8253),
    "WEST BOKARO": (23.7500, 85.5500), "SANKRAIL": (22.5700, 88.2200),
    "SILIGURI JN": (26.7271, 88.3953), "KUSHKHERA": (28.1200, 76.8300),
    "BMW-GAMHARIA": (22.8670, 86.0960), "BIDADA": (23.0300, 69.4400),
    "ALIBAGH": (18.6411, 72.8724), "BUXAR": (25.5600, 83.9800),
    "KARANJIA": (21.7600, 85.9700), "HALISHAHAR STOR": (22.9500, 88.4200),
    "DASKROI": (22.9800, 72.6300), "GANDHINAGAR": (23.2156, 72.6369),
    "GHAMARIA (KHARKAI)": (22.8600, 86.0900), "GWALIOR": (26.2183, 78.1828),
    "KARAKHENDRA": (21.5000, 85.5000), "PANVEL": (18.9894, 73.1175),
    "MALLAVALLI": (16.5500, 80.8500), "NEWDELHI": (28.6139, 77.2090),
    "SILIGURI TOWN": (26.7200, 88.4100), "SANGLI": (16.8524, 74.5815),
    "T.M INSIDE WORKS": (22.8000, 86.2050), "TARAPUR": (19.8300, 72.6800),
    "MUMBAI": (19.0760, 72.8777), "LUCKNOW": (26.8467, 80.9462),
    "GAYA": (24.7914, 85.0002), "MUZAFFARPUR": (26.1225, 85.3906),
}

# Approximate regional centroids by first two pincode digits (fallback).
PIN2_COORDS = {
    "11": (28.61, 77.21), "12": (28.40, 76.90), "13": (29.00, 76.00),
    "14": (30.60, 76.40), "15": (30.90, 75.00), "16": (30.73, 76.78),
    "17": (31.10, 77.20), "18": (32.70, 74.90), "19": (34.10, 74.80),
    "20": (28.60, 77.40), "21": (25.90, 81.00), "22": (25.50, 82.50),
    "23": (25.00, 83.00), "24": (29.20, 79.50), "25": (28.99, 77.70),
    "26": (26.85, 80.95), "27": (26.90, 83.00), "28": (26.80, 80.90),
    "30": (26.90, 75.80), "31": (27.20, 76.60), "32": (24.90, 74.60),
    "33": (28.00, 73.30), "34": (26.90, 70.90), "36": (22.30, 70.78),
    "37": (23.20, 69.70), "38": (23.02, 72.57), "39": (21.90, 73.00),
    "40": (19.07, 72.88), "41": (18.52, 73.86), "42": (19.99, 73.79),
    "43": (19.87, 75.34), "44": (21.15, 79.09), "45": (22.72, 75.86),
    "46": (23.26, 77.41), "47": (24.00, 78.50), "48": (23.80, 79.90),
    "49": (21.25, 81.63), "50": (17.38, 78.49), "51": (14.40, 78.80),
    "52": (16.30, 80.40), "53": (17.70, 83.20), "56": (12.97, 77.59),
    "57": (13.30, 76.60), "58": (15.36, 75.12), "59": (15.85, 74.50),
    "60": (13.08, 80.27), "61": (10.80, 78.70), "62": (9.90, 78.10),
    "63": (12.90, 79.10), "64": (11.00, 76.96), "67": (11.90, 75.40),
    "68": (9.93, 76.26), "69": (8.50, 76.90), "70": (22.57, 88.36),
    "71": (22.70, 88.20), "72": (23.30, 87.30), "73": (26.00, 88.80),
    "74": (22.20, 88.40), "75": (20.30, 85.82), "76": (19.30, 84.80),
    "77": (21.50, 84.00), "78": (26.14, 91.74), "79": (25.90, 93.00),
    "80": (25.59, 85.14), "81": (25.20, 86.80), "82": (24.80, 84.40),
    "83": (22.90, 86.00), "84": (25.90, 84.70), "85": (23.99, 85.36),
}


def _norm(name) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(r"\s+", " ", name.strip().upper())


def resolve(destination, pin_code):
    """Return (lat, lon) or (None, None)."""
    key = _norm(destination)
    if key in CITY_COORDS:
        return CITY_COORDS[key]
    pin = str(pin_code or "")
    pin = pin.split(".")[0].strip()
    if len(pin) == 6 and pin[:2] in PIN2_COORDS:
        return PIN2_COORDS[pin[:2]]
    return None, None


# ---------------------------------------------------------------------------
# State resolution
#
# The bubble maps were unreadable at destination grain: 126 destinations, most
# of them one or two trips, drawn as overlapping circles on a country map. The
# unit a planner actually reasons about is the STATE -- "Odisha is late, Tamil
# Nadu is not" -- so every trip is rolled up to one.
#
# State comes from the consignee PIN, not from the city name. Every trip in the
# corpus carries a 6-digit PIN, PIN blocks are allocated by the postal
# department along state lines, and unlike a node name a PIN cannot be spelled
# three different ways. The ranges below are on the FIRST THREE digits, which is
# the resolution at which the allocation is actually unambiguous: two digits
# would merge Uttarakhand into Uttar Pradesh (Bareilly 243 and Dehradun 248 both
# start "24") and Jharkhand into Bihar (both start "8").
# ---------------------------------------------------------------------------

# (first-3-digits low, high, state). Ordered; first match wins.
PIN3_STATE_RANGES: list[tuple[int, int, str]] = [
    (110, 110, "Delhi"),
    (121, 136, "Haryana"),
    (140, 152, "Punjab"),
    (160, 160, "Chandigarh"),
    (161, 166, "Punjab"),
    (171, 177, "Himachal Pradesh"),
    (180, 194, "Jammu & Kashmir"),
    # Uttarakhand is carved out of the UP block in three places.
    (246, 246, "Uttarakhand"),
    (248, 249, "Uttarakhand"),
    (262, 263, "Uttarakhand"),
    (201, 285, "Uttar Pradesh"),
    (301, 345, "Rajasthan"),
    (360, 396, "Gujarat"),
    (400, 402, "Maharashtra"),
    (403, 403, "Goa"),
    (404, 445, "Maharashtra"),
    (450, 488, "Madhya Pradesh"),
    (490, 497, "Chhattisgarh"),
    (500, 509, "Telangana"),
    (515, 535, "Andhra Pradesh"),
    (560, 591, "Karnataka"),
    (600, 643, "Tamil Nadu"),
    (670, 695, "Kerala"),
    (700, 743, "West Bengal"),
    (737, 737, "Sikkim"),
    (744, 744, "Andaman & Nicobar"),
    (751, 770, "Odisha"),
    (781, 788, "Assam"),
    (790, 792, "Arunachal Pradesh"),
    (793, 794, "Meghalaya"),
    (795, 795, "Manipur"),
    (796, 796, "Mizoram"),
    (797, 798, "Nagaland"),
    (799, 799, "Tripura"),
    # Jharkhand is carved out of the Bihar block, same as Uttarakhand above.
    (814, 816, "Jharkhand"),
    (825, 835, "Jharkhand"),
    (800, 855, "Bihar"),
]

# Approximate geographic centre of each state, for drawing one bubble per state.
# Deliberately the centroid and not the capital: a bubble means "this state",
# and anchoring it on the capital would imply the freight went there.
STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "Andhra Pradesh": (15.9129, 79.7400), "Arunachal Pradesh": (28.2180, 94.7278),
    "Assam": (26.2006, 92.9376), "Bihar": (25.0961, 85.3131),
    "Chandigarh": (30.7333, 76.7794), "Chhattisgarh": (21.2787, 81.8661),
    "Delhi": (28.7041, 77.1025), "Goa": (15.2993, 74.1240),
    "Gujarat": (22.2587, 71.1924), "Haryana": (29.0588, 76.0856),
    "Himachal Pradesh": (31.1048, 77.1734), "Jammu & Kashmir": (33.7782, 76.5762),
    "Jharkhand": (23.6102, 85.2799), "Karnataka": (15.3173, 75.7139),
    "Kerala": (10.8505, 76.2711), "Madhya Pradesh": (22.9734, 78.6569),
    "Maharashtra": (19.7515, 75.7139), "Manipur": (24.6637, 93.9063),
    "Meghalaya": (25.4670, 91.3662), "Mizoram": (23.1645, 92.9376),
    "Nagaland": (26.1584, 94.5624), "Odisha": (20.9517, 85.0985),
    "Punjab": (31.1471, 75.3412), "Rajasthan": (27.0238, 74.2179),
    "Sikkim": (27.5330, 88.5122), "Tamil Nadu": (11.1271, 78.6569),
    "Telangana": (18.1124, 79.0193), "Tripura": (23.9408, 91.9882),
    "Uttar Pradesh": (26.8467, 80.9462), "Uttarakhand": (30.0668, 79.0193),
    "West Bengal": (22.9868, 87.8550), "Andaman & Nicobar": (11.7401, 92.6586),
}

# Ping-level state codes (tta_trip_gps.s_wpnt1_st_abbr) -> the same names, so a
# PIN-derived state and an observed-trail state can be compared directly.
STATE_ABBR = {
    "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar",
    "CH": "Chandigarh", "CT": "Chhattisgarh", "DL": "Delhi", "DN": "Gujarat",
    "GA": "Goa", "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana",
    "JH": "Jharkhand", "JK": "Jammu & Kashmir", "KA": "Karnataka", "KL": "Kerala",
    "MH": "Maharashtra", "ML": "Meghalaya", "MN": "Manipur", "MP": "Madhya Pradesh",
    "MZ": "Mizoram", "NL": "Nagaland", "OR": "Odisha", "PB": "Punjab",
    "RJ": "Rajasthan", "SK": "Sikkim", "TN": "Tamil Nadu", "TS": "Telangana",
    "TR": "Tripura", "UK": "Uttarakhand", "UP": "Uttar Pradesh", "WB": "West Bengal",
}


def state_from_pin(pin_code) -> str | None:
    """State for a 6-digit Indian PIN, or None if it isn't one."""
    pin = str(pin_code or "").split(".")[0].strip()
    if len(pin) != 6 or not pin.isdigit():
        return None
    p3 = int(pin[:3])
    for lo, hi, name in PIN3_STATE_RANGES:
        if lo <= p3 <= hi:
            return name
    return None


def state_centroid(state) -> tuple[float | None, float | None]:
    return STATE_CENTROIDS.get(state or "", (None, None))
