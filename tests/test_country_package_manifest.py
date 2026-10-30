"""Native snapshots retain pinned reference data and its redistribution notices."""
from test_after80_backend import manager


def test_manifest_tracks_cldr_payload_and_both_legal_notices(tmp_path):
    package=tmp_path/'otravel'
    data=package/'data'
    data.mkdir(parents=True)
    (package/'__init__.py').write_text('',encoding='utf-8')
    (tmp_path/'pyproject.toml').write_text('[project]\nname="controlled"\n',encoding='utf-8')
    (data/'cldr_territories_ru_en.json').write_text('{}',encoding='utf-8')
    (data/'UNICODE-CLDR-LICENSE.txt').write_text('Controlled licence fixture',encoding='utf-8')
    (data/'CLDR-ATTRIBUTION.md').write_text('Controlled attribution fixture',encoding='utf-8')
    engine=manager.new_engine()
    initial=engine.source_manifest(tmp_path)
    paths={row['path'] for row in initial['files']}
    assert paths >= {'otravel/data/cldr_territories_ru_en.json',
        'otravel/data/UNICODE-CLDR-LICENSE.txt','otravel/data/CLDR-ATTRIBUTION.md'}
    (data/'CLDR-ATTRIBUTION.md').write_text('Changed fixture',encoding='utf-8')
    assert engine.source_manifest(tmp_path)['sha256']!=initial['sha256']
