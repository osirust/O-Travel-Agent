# Unicode CLDR territory names

The RU/EN country labels in `cldr_territories_ru_en.json` are derived from
[Unicode CLDR JSON](https://github.com/unicode-org/cldr-json) at commit
`7d2b9cb11cec08f4d333ad73209deb3df0711c3b`, under Unicode License V3.
The complete license and copyright notice are retained in
`UNICODE-CLDR-LICENSE.txt`. Source URLs and original file SHA-256 hashes are
embedded in the JSON's `source` field.

The subset includes the 249 current two-letter RU/EN territory labels whose
CLDR code mapping has an alpha-3 identifier and numeric code below 900.
Macroregions, unknown/reserved codes and deprecated-only labels are excluded.
Country labels and source short/variant names are retained; this is not a city
catalog or evidence of supplier availability.

`country_lexicon.py` adds only bounded Russian noun/adjective ending forms.
Irregular declensions, arbitrary transliteration and fuzzy spelling correction
are not provided. A literal mention does not assign a destination role, override
a selected place, establish nationality, or authorize a state change.
