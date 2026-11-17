# geojson-typed public input attribution

The geojson-typed unit fetches these public GeoJSON files at image build time from
pinned upstream commits (exact URLs, sizes and SHA-256 are in `payload-public.json`).
No input bytes are modified.

- Natural Earth vector data (nvkelso/natural-earth-vector, commit
  f1890d9f152c896d250a77557a5751a93d494776). Made with Natural Earth. Free vector and
  raster map data @ naturalearthdata.com. Natural Earth data is in the public domain.
- japan-topography (smartnews-smri/japan-topography, commit
  b403e71eb97f1fdf32f63d16bd485129f703855e), published by SmartNews Media Research
  Institute for free commercial and non-commercial use. The municipality files are
  processed from MLIT National Land Numerical Information:
  「国土数値情報（行政区域データ）」（国土交通省）（https://nlftp.mlit.go.jp/ksj/）を加工して作成.

The protected driver's coordinate visitor follows the approach of georust/geojson 1.0
(`geometry::deserialize::Coordinates`, MIT OR Apache-2.0).
