// Typed GeoJSON (RFC 7946) FeatureCollection schema for the geojson-typed unit.
// Coordinate decoding follows georust/geojson 1.0 `geometry::deserialize::Coordinates`
// (MIT/Apache-2.0): a `deserialize_any` visitor that discovers nesting depth. Members
// are the RFC 7946 set: unknown members are rejected, `type`/`features`/`geometry` are
// required and duplicates are errors. Every value (each coordinate as exact f64 bits,
// every property) is written through DeserializeSeed straight into one owned output
// buffer, a flat GeoArrow/FlatGeobuf-style layout with nesting counts. Unescaped
// strings are taken borrowed. No serde_json type outlives the timed region.
use serde::de::{self, DeserializeSeed, Deserializer, MapAccess, SeqAccess, Visitor};
use std::fmt;

pub struct Sink<'o>(pub &'o mut Vec<u8>);
impl Sink<'_> {
    #[inline] fn b(&mut self, x: u8) { self.0.push(x) }
    #[inline] fn f(&mut self, x: f64) { self.0.extend_from_slice(&x.to_bits().to_le_bytes()) }
    #[inline] fn u(&mut self, x: u64) { self.0.extend_from_slice(&x.to_le_bytes()) }
    #[inline] fn s(&mut self, x: &str) { self.0.extend_from_slice(&(x.len() as u32).to_le_bytes()); self.0.extend_from_slice(x.as_bytes()) }
    #[inline] fn open(&mut self, tag: u8) -> usize { self.b(tag); let p = self.0.len(); self.0.extend_from_slice(&[0; 4]); p }
    #[inline] fn close(&mut self, at: usize, n: u32) { self.0[at..at + 4].copy_from_slice(&n.to_le_bytes()) }
    fn r(&mut self) -> Sink<'_> { Sink(self.0) }
}

macro_rules! fields {
    ($name:ident { $($var:ident = $lit:literal),* }) => {
        #[derive(Clone, Copy, PartialEq)]
        #[repr(u8)]
        enum $name { $($var),* }
        impl<'de> de::Deserialize<'de> for $name {
            fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
                struct V;
                impl<'de> Visitor<'de> for V {
                    type Value = $name;
                    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a known member name") }
                    fn visit_str<E: de::Error>(self, v: &str) -> Result<$name, E> {
                        match v { $($lit => Ok($name::$var),)* _ => Err(E::unknown_field(v, &[$($lit),*])) }
                    }
                }
                d.deserialize_identifier(V)
            }
        }
    };
}
fields!(CollKey { Type = "type", Name = "name", Crs = "crs", Bbox = "bbox", Features = "features" });
fields!(FeatKey { Type = "type", Id = "id", Bbox = "bbox", Properties = "properties", Geometry = "geometry" });
fields!(GeomKey { Type = "type", Bbox = "bbox", Coordinates = "coordinates", Geometries = "geometries" });

fn once<E: de::Error>(seen: &mut u32, bit: u8, name: &'static str) -> Result<(), E> {
    if *seen & (1 << bit) != 0 { return Err(E::duplicate_field(name)); }
    *seen |= 1 << bit; Ok(())
}

/// Literal tag check ("FeatureCollection", "Feature"), borrowed when possible.
struct Tag(&'static [&'static str]);
impl<'de> DeserializeSeed<'de> for Tag {
    type Value = u8;
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<u8, D::Error> {
        struct V(&'static [&'static str]);
        impl<'de> Visitor<'de> for V {
            type Value = u8;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a GeoJSON type name") }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<u8, E> {
                self.0.iter().position(|t| *t == v).map(|i| i as u8).ok_or_else(|| E::unknown_variant(v, self.0))
            }
        }
        d.deserialize_str(V(self.0))
    }
}
const GEOMETRY_TYPES: &[&str] = &["Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection"];

pub struct CollectionSeed<'a, 'o>(pub &'a mut Sink<'o>);
impl<'de> DeserializeSeed<'de> for CollectionSeed<'_, '_> {
    type Value = u64;
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<u64, D::Error> { d.deserialize_map(self) }
}
impl<'de> Visitor<'de> for CollectionSeed<'_, '_> {
    type Value = u64;
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a FeatureCollection") }
    fn visit_map<A: MapAccess<'de>>(self, mut m: A) -> Result<u64, A::Error> {
        let o = self.0; let mut seen = 0u32; let mut features = 0u64;
        while let Some(k) = m.next_key::<CollKey>()? {
            once(&mut seen, k as u8, ["type", "name", "crs", "bbox", "features"][k as usize])?;
            o.b(b'K'); o.b(k as u8);
            match k {
                CollKey::Type => { m.next_value_seed(Tag(&["FeatureCollection"]))?; }
                CollKey::Name => m.next_value_seed(OptStr(o.r()))?,
                CollKey::Crs => m.next_value_seed(Any(o.r()))?,
                CollKey::Bbox => m.next_value_seed(Nums(o.r()))?,
                CollKey::Features => features = m.next_value_seed(Features(o.r()))?,
            }
        }
        if seen & 1 == 0 { return Err(de::Error::missing_field("type")); }
        if seen & (1 << CollKey::Features as u8) == 0 { return Err(de::Error::missing_field("features")); }
        Ok(features)
    }
}

struct Features<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for Features<'_> {
    type Value = u64;
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<u64, D::Error> { d.deserialize_seq(self) }
}
impl<'de> Visitor<'de> for Features<'_> {
    type Value = u64;
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a feature array") }
    fn visit_seq<A: SeqAccess<'de>>(mut self, mut s: A) -> Result<u64, A::Error> {
        let at = self.0.open(b'['); let mut n = 0u32;
        while s.next_element_seed(Feature(self.0.r()))?.is_some() { n += 1; }
        self.0.close(at, n); Ok(n as u64)
    }
}

struct Feature<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for Feature<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_map(self) }
}
impl<'de> Visitor<'de> for Feature<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a Feature") }
    fn visit_map<A: MapAccess<'de>>(mut self, mut m: A) -> Result<(), A::Error> {
        let o = &mut self.0; let mut seen = 0u32;
        o.b(b'F');
        while let Some(k) = m.next_key::<FeatKey>()? {
            once(&mut seen, k as u8, ["type", "id", "bbox", "properties", "geometry"][k as usize])?;
            o.b(b'K'); o.b(k as u8);
            match k {
                FeatKey::Type => { m.next_value_seed(Tag(&["Feature"]))?; }
                FeatKey::Id => m.next_value_seed(Any(o.r()))?,
                FeatKey::Bbox => m.next_value_seed(Nums(o.r()))?,
                FeatKey::Properties => m.next_value_seed(Any(o.r()))?,
                FeatKey::Geometry => m.next_value_seed(OptGeometry(o.r()))?,
            }
        }
        if seen & 1 == 0 { return Err(de::Error::missing_field("type")); }
        if seen & (1 << FeatKey::Geometry as u8) == 0 { return Err(de::Error::missing_field("geometry")); }
        o.b(b'f'); Ok(())
    }
}

struct OptGeometry<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for OptGeometry<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_option(self) }
}
impl<'de> Visitor<'de> for OptGeometry<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a Geometry or null") }
    fn visit_none<E>(mut self) -> Result<(), E> { self.0.b(0); Ok(()) }
    fn visit_unit<E>(mut self) -> Result<(), E> { self.0.b(0); Ok(()) }
    fn visit_some<D: Deserializer<'de>>(mut self, d: D) -> Result<(), D::Error> { self.0.b(1); d.deserialize_map(Geometry(self.0)) }
}

struct Geometry<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for Geometry<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_map(self) }
}
impl<'de> Visitor<'de> for Geometry<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a Geometry") }
    fn visit_map<A: MapAccess<'de>>(mut self, mut m: A) -> Result<(), A::Error> {
        let o = &mut self.0; let mut seen = 0u32;
        o.b(b'G');
        while let Some(k) = m.next_key::<GeomKey>()? {
            once(&mut seen, k as u8, ["type", "bbox", "coordinates", "geometries"][k as usize])?;
            o.b(b'K'); o.b(k as u8);
            match k {
                GeomKey::Type => { let t = m.next_value_seed(Tag(GEOMETRY_TYPES))?; o.b(t); }
                GeomKey::Bbox => m.next_value_seed(Nums(o.r()))?,
                GeomKey::Coordinates => m.next_value_seed(Coords(o.r()))?,
                GeomKey::Geometries => m.next_value_seed(Geometries(o.r()))?,
            }
        }
        if seen & 1 == 0 { return Err(de::Error::missing_field("type")); }
        o.b(b'g'); Ok(())
    }
}

struct Geometries<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for Geometries<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_seq(self) }
}
impl<'de> Visitor<'de> for Geometries<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a geometry array") }
    fn visit_seq<A: SeqAccess<'de>>(mut self, mut s: A) -> Result<(), A::Error> {
        let at = self.0.open(b'['); let mut n = 0u32;
        while s.next_element_seed(Geometry(self.0.r()))?.is_some() { n += 1; }
        self.0.close(at, n); Ok(())
    }
}

/// Coordinates: arbitrarily nested arrays of numbers (Position = [x, y, ...]).
struct Coords<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for Coords<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_any(self) }
}
impl<'de> Visitor<'de> for Coords<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a number or coordinate array") }
    fn visit_f64<E>(mut self, v: f64) -> Result<(), E> { self.0.b(b'd'); self.0.f(v); Ok(()) }
    fn visit_u64<E>(mut self, v: u64) -> Result<(), E> { self.0.b(b'u'); self.0.u(v); Ok(()) }
    fn visit_i64<E>(mut self, v: i64) -> Result<(), E> { self.0.b(b'i'); self.0.u(v as u64); Ok(()) }
    fn visit_seq<A: SeqAccess<'de>>(mut self, mut s: A) -> Result<(), A::Error> {
        let at = self.0.open(b'['); let mut n = 0u32;
        while s.next_element_seed(Coords(self.0.r()))?.is_some() { n += 1; }
        self.0.close(at, n); Ok(())
    }
}

/// bbox: array of numbers.
struct Nums<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for Nums<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_seq(self) }
}
impl<'de> Visitor<'de> for Nums<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a number array") }
    fn visit_seq<A: SeqAccess<'de>>(mut self, mut s: A) -> Result<(), A::Error> {
        let at = self.0.open(b'['); let mut n = 0u32;
        while let Some(v) = s.next_element::<f64>()? { self.0.f(v); n += 1; }
        self.0.close(at, n); Ok(())
    }
}

struct OptStr<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for OptStr<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_option(self) }
}
impl<'de> Visitor<'de> for OptStr<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a string or null") }
    fn visit_none<E>(mut self) -> Result<(), E> { self.0.b(0); Ok(()) }
    fn visit_unit<E>(mut self) -> Result<(), E> { self.0.b(0); Ok(()) }
    fn visit_some<D: Deserializer<'de>>(mut self, d: D) -> Result<(), D::Error> { self.0.b(1); d.deserialize_str(Str(self.0)) }
}
struct Str<'o>(Sink<'o>);
impl<'de> Visitor<'de> for Str<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("a string") }
    fn visit_str<E>(mut self, v: &str) -> Result<(), E> { self.0.s(v); Ok(()) }
}

/// Any JSON value (properties, crs, id), written as tagged records.
struct Any<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for Any<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_any(self) }
}
impl<'de> Visitor<'de> for Any<'_> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("any JSON value") }
    fn visit_unit<E>(mut self) -> Result<(), E> { self.0.b(b'z'); Ok(()) }
    fn visit_bool<E>(mut self, v: bool) -> Result<(), E> { self.0.b(b'b'); self.0.b(v as u8); Ok(()) }
    fn visit_u64<E>(mut self, v: u64) -> Result<(), E> { self.0.b(b'u'); self.0.u(v); Ok(()) }
    fn visit_i64<E>(mut self, v: i64) -> Result<(), E> { self.0.b(b'i'); self.0.u(v as u64); Ok(()) }
    fn visit_f64<E>(mut self, v: f64) -> Result<(), E> { self.0.b(b'd'); self.0.f(v); Ok(()) }
    fn visit_str<E>(mut self, v: &str) -> Result<(), E> { self.0.b(b's'); self.0.s(v); Ok(()) }
    fn visit_seq<A: SeqAccess<'de>>(mut self, mut s: A) -> Result<(), A::Error> {
        let at = self.0.open(b'['); let mut n = 0u32;
        while s.next_element_seed(Any(self.0.r()))?.is_some() { n += 1; }
        self.0.close(at, n); Ok(())
    }
    fn visit_map<A: MapAccess<'de>>(mut self, mut m: A) -> Result<(), A::Error> {
        let at = self.0.open(b'{'); let mut n = 0u32;
        while m.next_key_seed(KeySeed(self.0.r()))?.is_some() { m.next_value_seed(Any(self.0.r()))?; n += 1; }
        self.0.close(at, n); Ok(())
    }
}
struct KeySeed<'o>(Sink<'o>);
impl<'de> DeserializeSeed<'de> for KeySeed<'_> {
    type Value = ();
    fn deserialize<D: Deserializer<'de>>(self, d: D) -> Result<(), D::Error> { d.deserialize_str(Str(self.0)) }
}
