"""Lightweight free-text and geographic field analysis (no heavy NLP, no network).

Text: character and word lengths, the share of empty values, the most frequent tokens and bigrams after removing
stop-words of the detected language, and how text length or presence goes with the target. Tokens are runs of
letters or digits (Unicode classes), lower-cased; pure numbers and one-character tokens are left out.
Language detection: the share of tokens that are stop-words of each built-in language (English, German, French,
Spanish, Italian, Portuguese, Dutch); the highest share wins when it is at least 5%, otherwise "unknown" and
English stop-words are used.

Geo: latitude/longitude pairs are recognised from disclosed name hints (lat, latitude / lon, lng, long, longitude)
plus valid ranges; country codes from ISO 3166-1 alpha-2 or alpha-3 lists (at least 75% of distinct values and 90% of
rows, at least 3 distinct values). Checks: values out of range, (0, 0) points, one coordinate missing, invalid codes.
Points are counted in grid cells whose size is chosen so that between 5 and 200 cells are occupied.
"""
import re

STOPWORDS = {
    'english': 'a about after all also an and any are as at be been but by can could did do does for from had has have he her his how i if in into is it '
               'its just me more most my no not of on one or other our out she so some than that the their them then there these they this to too up us '
               'was we were what when which who will with would you your',
    'german': 'aber alle als am an auch auf aus bei bin bis das dass dem den der des die doch du ein eine einem einen einer es für hat hatte ich ihr im '
              'in ist ja kein mit nach nicht noch nur oder sich sie sind so über um und uns von vor war was wenn wie wir wird zu zum zur',
    'french': 'au aux avec ce ces dans de des du elle en est et eux il ils je la le les leur lui ma mais me mes moi mon ne nos notre nous on ou par pas '
              'pour qu que qui sa se ses son sur ta te tes toi ton tu un une vos votre vous été être avoir fait très',
    'spanish': 'a al algo como con de del el ella en entre era es esta este fue ha hay la las le lo los me mi muy más no nos o para pero por que qué '
               'se sin sobre su sus también te tu un una uno y ya está están',
    'italian': 'a ad al alla anche che chi ci come con da dal dalla del della di e ed gli ha ho i il in io la le lei lo lui ma mi mio ne nel nella non '
               'per più quando se si sono su sua suo ti tu un una uno è',
    'portuguese': 'a ao aos as com como da das de do dos e ela ele em entre era essa esse está eu foi isso já lhe mais mas me meu muito na nas não no '
                  'nos o os ou para pela pelo por que se sem seu sua também te um uma você é',
    'dutch': 'aan al als bij dan dat de die dit door een en er had heb heeft het hij hoe ik in is je kan maar me met mij na naar niet nog nu of om '
             'ons ook op over te tot uit van voor was wat we wel wij zal ze zich zijn zo',
}
STOPWORDS = {language: frozenset(words.split()) for language, words in STOPWORDS.items()}
TOKEN_PATTERN = r'[^\p{L}\p{N}]+'

ALPHA2 = frozenset((
    'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM '
    'CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY '
    'HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF '
    'MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE '
    'RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ '
    'VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW XK').split())
ALPHA3 = frozenset((
    'AND ARE AFG ATG AIA ALB ARM AGO ATA ARG ASM AUT AUS ABW ALA AZE BIH BRB BGD BEL BFA BGR BHR BDI BEN BLM BMU BRN BOL BES BRA BHS BTN BVT BWA '
    'BLR BLZ CAN CCK COD CAF COG CHE CIV COK CHL CMR CHN COL CRI CUB CPV CUW CXR CYP CZE DEU DJI DNK DMA DOM DZA ECU EST EGY ESH ERI ESP ETH FIN '
    'FJI FLK FSM FRO FRA GAB GBR GRD GEO GUF GGY GHA GIB GRL GMB GIN GLP GNQ GRC SGS GTM GUM GNB GUY HKG HMD HND HRV HTI HUN IDN IRL ISR IMN IND '
    'IOT IRQ IRN ISL ITA JEY JAM JOR JPN KEN KGZ KHM KIR COM KNA PRK KOR KWT CYM KAZ LAO LBN LCA LIE LKA LBR LSO LTU LUX LVA LBY MAR MCO MDA MNE '
    'MAF MDG MHL MKD MLI MMR MNG MAC MNP MTQ MRT MSR MLT MUS MDV MWI MEX MYS MOZ NAM NCL NER NFK NGA NIC NLD NOR NPL NRU NIU NZL OMN PAN PER PYF '
    'PNG PHL PAK POL SPM PCN PRI PSE PRT PLW PRY QAT REU ROU SRB RUS RWA SAU SLB SYC SDN SWE SGP SHN SVN SJM SVK SLE SMR SEN SOM SUR SSD STP SLV '
    'SXM SYR SWZ TCA TCD ATF TGO THA TJK TKL TLS TKM TUN TON TUR TTO TUV TWN TZA UKR UGA UMI USA URY UZB VAT VCT VEN VGB VIR VNM VUT WLF WSM YEM '
    'MYT ZAF ZMB ZWE XKX').split())
LAT_HINTS = ('lat', 'latitude')
LON_HINTS = ('lon', 'lng', 'long', 'longitude')


def words_of(name):
    return re.sub(r'[^a-z0-9]+', ' ', re.sub(r'([a-z])([A-Z])', r'\1 \2', name).lower()).split()


def detect_language(token_counts):
    """(language, share of tokens that are its stop-words) from {token: count}."""
    total = sum(token_counts.values())
    if not total: return 'unknown', 0.0
    shares = {lang: sum(n for t, n in token_counts.items() if t in words) / total for lang, words in STOPWORDS.items()}
    language = max(shares, key=lambda lang: (shares[lang], lang))
    return (language if shares[language] >= 0.05 else 'unknown'), shares[language]


def stopwords_for(language):
    return STOPWORDS.get(language, STOPWORDS['english'])


def country_code_kind(values):
    """'alpha-2', 'alpha-3' or None from [(value, rows)] of a categorical field."""
    present = [(str(v), n) for v, n in values if v is not None]
    if len({v for v, _ in present}) < 3: return None
    rows = sum(n for _, n in present) or 1
    for kind, codes in (('ISO 3166-1 alpha-2', ALPHA2), ('ISO 3166-1 alpha-3', ALPHA3)):
        valid = [(v, n) for v, n in present if v in codes]
        if len(valid) >= 0.75 * len(present) and sum(n for _, n in valid) >= 0.9 * rows: return kind
    return None


def coordinate_pairs(facts):
    """[(latitude field, longitude field)] from numeric fields whose names hint at coordinates."""
    lats = [n for n, f in facts.items() if f['type'] == 'numeric' and set(words_of(n)) & set(LAT_HINTS)]
    lons = [n for n, f in facts.items() if f['type'] == 'numeric' and set(words_of(n)) & set(LON_HINTS)]
    pairs = []
    for lat in lats:
        rest = [lon for lon in lons if lon not in {p[1] for p in pairs}]
        if not rest: break
        # Prefer the longitude whose name differs from the latitude's only in the hint word.
        stem = [w for w in words_of(lat) if w not in LAT_HINTS]
        rest.sort(key=lambda lon: ([w for w in words_of(lon) if w not in LON_HINTS] != stem, list(facts).index(lon)))
        pairs.append((lat, rest[0]))
    return pairs


def cell_size(span_lat, span_lon, occupied):
    """Grid cell size in degrees: the finest of 10, 5, 1, 0.5, 0.1, 0.01 with at most 200 occupied cells (from estimates)."""
    for size in (0.01, 0.1, 0.5, 1, 5, 10):
        if occupied(size) <= 200: return size
    return 10
