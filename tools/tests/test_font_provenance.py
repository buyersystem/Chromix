import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest
from test_followup_native import native_sources
from test_fingerprint_canvas import CXX, sanitizer_env, sanitizer_flags
from test_fingerprint_features import block

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk/python"))
from chromix import _font_provenance as font


def sfnt(tables, base=0):
    count = len(tables)
    data_offset = 12 + count * 16
    directory, payload = b"", b""
    for tag, data in tables.items():
        directory += struct.pack(">4sIII", tag, 0, base + data_offset + len(payload), len(data))
        payload += data
    return struct.pack(">4sHHHH", b"\0\1\0\0", count, 0, 0, 0) + directory + payload


def test_canonical_digest_and_collection_repacking(tmp_path):
    tables = {b"head": bytes(range(16)), b"glyf": b"shape", b"DSIG": b"container-signature"}
    adjusted = {b"glyf": b"shape", b"head": bytes(range(8)) + b"abcd" + bytes(range(12, 16))}
    assert font.table_hash(tables) == font.table_hash(adjusted)
    assert font.table_hash(tables) != font.table_hash({**tables, b"glyf": b"other"})
    path = tmp_path / "face.ttf"
    path.write_bytes(sfnt(tables))
    record = font.font_file_record(path)
    assert record["faces"][0]["table_hash"] == font.table_hash(tables)
    collection = tmp_path / "collection.ttc"
    collection.write_bytes(b"ttcf" + struct.pack(">III", 0x10000, 1, 16) + sfnt(tables, 16))
    assert font.font_file_record(collection)["faces"] == record["faces"]


@pytest.mark.parametrize("data", [b"bad", b"ttcf" + b"\0" * 20,
    b"\0\1\0\0" + struct.pack(">H", 513) + b"\0" * 10,
    sfnt({b"head": b"short"}), sfnt({b"glyf": b"shape"})[:-1]])
def test_malformed_font_bounds(tmp_path, data):
    path = tmp_path / "bad.ttf"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        font.font_file_record(path)


def test_names_never_establish_binding_and_ambiguity_stays_unverified(tmp_path):
    path = tmp_path / "face.ttf"
    path.write_bytes(sfnt({b"glyf": b"shape"}))
    record = font.font_file_record(path)
    sample = [{"platformFonts": [{"postScriptName": "SameName", "glyphCount": 4}]}]
    assert font.bind_font_sources(sample, [record])["file_binding_verified"] is False
    sample[0]["platformFonts"][0].update(fontTableHash=record["faces"][0]["table_hash"],
                                      fontTableHashAlgorithm=font.ALGORITHM)
    assert font.bind_font_sources(sample, [record])["file_binding_verified"] is True
    other = {**record, "path": "different-file.ttf"}
    result = font.bind_font_sources(sample, [record, other])
    assert result["file_binding_verified"] is False
    assert result["bindings"][0]["status"] == "content_matched_multiple_files"
    assert result["rasterization_equivalence"] == "not_verified"


def test_native_digest_byte_contract(tmp_path, native_sources):
    if not CXX:
        pytest.skip("C++20 compiler required")
    helper = block(native_sources["0152"], "String ComputePlatformFontTableHash(")
    # Digestor is a transcript collector here: compare every native hash input
    # byte to the independent Python encoder, not a fake cryptographic digest.
    shim = r'''
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <map>
#include <span>
#include <string>
#include <vector>
#include <cstring>
using SkFontTableTag=uint32_t;
constexpr uint32_t SkSetFourByteTag(char a,char b,char c,char d){return uint32_t(a)<<24|uint32_t(b)<<16|uint32_t(c)<<8|uint32_t(d);}
struct String:std::string {using std::string::string;static String FromUTF8(const std::string& v){return String(v.c_str());}};
using DigestValue=std::vector<uint8_t>;
constexpr int kHashAlgorithmSha256=1;
struct Digestor {
  std::vector<uint8_t> bytes;
  explicit Digestor(int){}
  template<class R> bool Update(const R& r){bytes.insert(bytes.end(),r.begin(),r.end());return true;}
  bool Finish(DigestValue& out){out=bytes;return true;}
};
namespace base {
template<size_t N> auto as_byte_span(const char(&v)[N]){return std::span(reinterpret_cast<const uint8_t*>(v),N);}
inline auto U32ToBigEndian(uint32_t v){std::array<uint8_t,4> out{};for(int i=3;i>=0;--i){out[i]=v&255;v>>=8;}return out;}
inline auto U64ToBigEndian(uint64_t v){std::array<uint8_t,8> out{};for(int i=7;i>=0;--i){out[i]=v&255;v>>=8;}return out;}
inline std::string HexEncodeLower(const std::vector<uint8_t>& b){std::string out;for(auto v:b){out+="0123456789abcdef"[v>>4];out+="0123456789abcdef"[v&15];}return out;}
}
struct SkTypeface {
  std::map<uint32_t,std::vector<uint8_t>> tables;
  int mode=0;
  int countTables()const{return mode==1?513:static_cast<int>(tables.size());}
  int getTableTags(uint32_t* tags)const{int i=0;for(auto it=tables.rbegin();it!=tables.rend();++it)tags[i++]=it->first;return i;}
  size_t getTableSize(uint32_t tag)const{return mode==2?65*1024*1024:tables.at(tag).size();}
  size_t getTableData(uint32_t tag,size_t,size_t n,void* out)const{if(mode==3)return 0;std::memcpy(out,tables.at(tag).data(),n);return n;}
};
'''
    main = r'''
int main(int argc,char**argv){
  SkTypeface face;face.mode=argc>1?std::stoi(argv[1]):0;
  face.tables[SkSetFourByteTag('h','e','a','d')]={0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15};
  face.tables[SkSetFourByteTag('g','l','y','f')]={'s','h','a','p','e'};
  face.tables[SkSetFourByteTag('D','S','I','G')]={'s','i','g'};
  std::cout<<ComputePlatformFontTableHash(face);
}
'''
    unit, binary = tmp_path / "font.cc", tmp_path / ("font.exe" if os.name == "nt" else "font")
    unit.write_text(shim + helper + main, encoding="utf-8")
    result = subprocess.run([CXX, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", *sanitizer_flags(),
                             str(unit), "-o", str(binary)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    for mode in range(4):
        result = subprocess.run([str(binary), str(mode)], capture_output=True, text=True, timeout=10, env=sanitizer_env())
        assert result.returncode == 0, result.stderr
        expected = b"" if mode else b"".join(font.canonical_table_bytes({b"head": bytes(range(16)), b"glyf": b"shape", b"DSIG": b"sig"}))
        assert bytes.fromhex(result.stdout) == expected


def test_native_protocol_uses_typeface_content_not_names(native_sources):
    source = native_sources["0152"]
    assert "PlatformData().UniqueID()" in source
    assert "ComputePlatformFontTableHash(*typeface)" in source
    assert "setFontTableHashAlgorithm" in source
    assert "experimental optional string fontTableHash" in native_sources["0153"]
