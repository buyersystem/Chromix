"""Execute final family resolution/fallback with an invented font backend, not a font corpus."""
import pytest

from test_backend_completion import EVIDENCE, backend_binary, final_sources, run
from test_backend_media import compile_configured, method
from test_fingerprint_config import config_binary
from test_fingerprint_features import block
from test_font_correctness import FONT_RUNTIME_STUBS


EXTRA = r'''
struct SkTypeface {
  bool empty=false;
  static SkTypeface MakeEmpty() {return {true};}
};
struct ResolvedFontFeatures {};
struct FontPlatformData {
  std::string family;
  explicit FontPlatformData(std::string name="") : family(std::move(name)) {}
  FontPlatformData(SkTypeface face,const std::string&,float,bool,bool,int,ResolvedFontFeatures,int)
    :family(face.empty?"<empty>":"<unexpected>") {}
  String FontFamilyName() const {return {family};}
};
template<class T,class... A> T* MakeGarbageCollected(A&&... args) {
  static std::vector<std::unique_ptr<T>> allocated;
  allocated.push_back(std::make_unique<T>(std::forward<A>(args)...));return allocated.back().get();
}
struct SimpleFontData {
  const FontPlatformData* data=nullptr;
  bool GlyphForCharacter(int character) const {
    return (data->family=="Allowed"&&character=='A')||(data->family=="Emoji"&&character==0x1f642);
  }
};
struct FontCache;
struct NativeBackend {
  std::map<std::string,FontPlatformData> installed;
  std::vector<std::string> attempts;
  const FontPlatformData* GetOrCreateFontPlatformData(FontCache*,const FontDescription&,
      const FontFaceCreationParams& params,AlternateFontName) {
    const auto name=params.Family().GetString().Utf8();attempts.push_back(name);
    const auto found=installed.find(name);
    return found==installed.end()?nullptr:&found->second;
  }
};
using UChar32=int;
enum class FontFallbackPriority {kText,kEmoji};
bool IsNonTextFallbackPriority(FontFallbackPriority p) {return p==FontFallbackPriority::kEmoji;}
struct Character {
  static bool IsPrivateUse(UChar32 c) {return c==0xe000;}
  static bool IsNonCharacter(UChar32 c) {return c==0xffff;}
};
namespace base {
struct TimeDelta {};
struct ElapsedTimer {TimeDelta Elapsed() const {return {};}};
}
using UErrorCode=int;
using UScriptCode=int;
constexpr int U_ZERO_ERROR=0,USCRIPT_INVALID_CODE=-1;
int uscript_getScript(UChar32,UErrorCode*) {return 1;}
bool U_FAILURE(UErrorCode code) {return code!=0;}
struct FontPerformance {static void AddSystemFallbackFontTime(int,bool,base::TimeDelta) {}};
struct FontCache {
  NativeBackend font_platform_data_cache_;
  SimpleFontData last;
  int native_fallbacks=0;
  const FontPlatformData* GetFontPlatformData(const FontDescription&,const FontFaceCreationParams&,AlternateFontName);
  const FontPlatformData* SystemFontPlatformData(const FontDescription& desc) {
    return GetFontPlatformData(desc,FontFaceCreationParams(AtomicString("Host GUI")),AlternateFontName::kNoAlternate);
  }
  static bool IsUxrFontFamilyAllowed(const String&);
  const SimpleFontData* UxrRestrictedLastResortFont(const FontDescription&);
  const SimpleFontData* FallbackFontForCharacter(const FontDescription&,UChar32,const SimpleFontData*,FontFallbackPriority);
  const SimpleFontData* GetFontData(const FontDescription& desc,const AtomicString& family,
                                  AlternateFontName alt=AlternateFontName::kAllowAlternate) {
    auto* data=GetFontPlatformData(desc,FontFaceCreationParams(family),alt);
    return data?FontDataFromFontPlatformData(data,false):nullptr;
  }
  const SimpleFontData* FontDataFromFontPlatformData(const FontPlatformData* data,bool) {
    last.data=data;return &last;
  }
  const SimpleFontData* PlatformFallbackFontForCharacter(const FontDescription&,UChar32,
      const SimpleFontData*,FontFallbackPriority) {
    ++native_fallbacks;return FontDataFromFontPlatformData(&font_platform_data_cache_.installed.at("Host GUI"),false);
  }
};
'''

MAIN = r'''
int main(int argc,char**argv) {
  assert(argc==3);
  auto& config=base::UxrConfig::GetInstance();
  const bool restricted=std::string(argv[1])=="restricted";
  const bool missing=std::string(argv[2])=="missing";
  assert(config.SetAll({{"uxr-font-policy",argv[1]}, {"uxr-font-whitelist","Allowed, Emoji"},
                       {"uxr-platform","windows"},{"uxr-synthetic-device-tests","true"}}));
  FontCache cache;
  auto& installed=cache.font_platform_data_cache_.installed;
  installed.emplace("Host GUI",FontPlatformData("Host GUI"));
  installed.emplace("serif",FontPlatformData("Host GUI"));
  installed.emplace("AllowedAlias",FontPlatformData("Host GUI"));
  if(!missing) {
    installed.emplace("Allowed",FontPlatformData("Allowed"));
    installed.emplace("Allowed-Regular",FontPlatformData("Allowed"));
    installed.emplace("Emoji",FontPlatformData("Emoji"));
  }
  FontDescription description;
  for(auto mode:{AlternateFontName::kAllowAlternate,AlternateFontName::kNoAlternate,
                 AlternateFontName::kLocalUniqueFace,AlternateFontName::kLastResort}) {
    assert(bool(cache.GetFontPlatformData(description,FontFaceCreationParams(AtomicString("AllowedAlias")),mode))==!restricted);
    assert(bool(cache.GetFontPlatformData(description,FontFaceCreationParams(AtomicString("Allowed-Regular")),mode))==!missing);
    assert(bool(cache.GetFontPlatformData(description,FontFaceCreationParams(AtomicString("serif")),mode))==!restricted);
  }
  assert(UxrSystemFontSubstitute(AtomicString("serif"))==nullptr);
  assert(FontCache::IsUxrFontFamilyAllowed(String{"aLLoWed"}));
  assert(FontCache::IsUxrFontFamilyAllowed(String{"Host GUI"})==!restricted);
  assert(FontCache::IsUxrFontFamilyAllowed(String{"AllowedAlias"})==!restricted);
  auto* fallback=cache.UxrRestrictedLastResortFont(description);
  if(restricted) {
    assert(fallback);
    assert(fallback->data->family==(missing?"<empty>":"Allowed"));
  } else assert(!fallback);
  auto text=cache.FallbackFontForCharacter(description,'A',nullptr,FontFallbackPriority::kText);
  if(restricted) assert(missing?!text:(text&&text->data->family=="Allowed"));
  else assert(text&&text->data->family=="Host GUI");
  auto emoji=cache.FallbackFontForCharacter(description,0x1f642,nullptr,FontFallbackPriority::kEmoji);
  if(restricted) assert(missing?!emoji:(emoji&&emoji->data->family=="Emoji"));
  else assert(emoji&&emoji->data->family=="Host GUI");
  auto outside=cache.FallbackFontForCharacter(description,0x4e00,nullptr,FontFallbackPriority::kText);
  assert(bool(outside)==!restricted);
  assert(cache.FallbackFontForCharacter(description,0xe000,nullptr,FontFallbackPriority::kText)==nullptr);
  assert(cache.FallbackFontForCharacter(description,0xffff,nullptr,FontFallbackPriority::kText)==nullptr);
  assert(cache.native_fallbacks==(restricted?0:3));
}
'''


@pytest.fixture(scope='module', params=[0, 1])
def font_binary(tmp_path_factory, backend_binary, request):
    support = FONT_RUNTIME_STUBS
    for signature in ('struct UxrConfig {', 'struct FontPlatformData {', 'struct NativeBackend {', 'struct FontCache {'):
        support = support.replace(block(support, signature) + ';', '')
    support = support.replace('struct String {', '''struct String {
  static String FromUtf8(const std::string& s) {return {s};}''')
    support = support.replace('  String value;\n  explicit AtomicString',
                              '  String value;\n  explicit AtomicString(String text):value(text) {}\n  explicit AtomicString')
    support = support.replace('struct FontDescription { int marker = 42; };', '''struct FontDescription {
  int marker=42;
  float EffectiveFontSize() const {return 16;}
  int TextRendering() const {return 0;}
  int Orientation() const {return 0;}
  bool SubpixelAscentDescent() const {return false;}
};''')
    support = '#include "base/uxr_config.h"\n#include <memory>\n#include <map>\n' + support + EXTRA
    signatures = ('bool UxrFontFamilyIsGeneric(', 'const char* UxrSystemFontSubstitute(', 'bool UxrFontFamilyAllowed(',
                  'const FontPlatformData* FontCache::GetFontPlatformData(', 'bool FontCache::IsUxrFontFamilyAllowed(',
                  'const SimpleFontData* FontCache::UxrRestrictedLastResortFont(',
                  'const SimpleFontData* FontCache::FallbackFontForCharacter(')
    code = f'#define IS_MAC {request.param}\n' + support + ''.join(method(s) for s in signatures) + MAIN
    return compile_configured(tmp_path_factory.mktemp('font-parent') / 'src', backend_binary.parent, code)


@pytest.mark.parametrize('mode', ['native', 'restricted'])
@pytest.mark.parametrize('inventory', ['installed', 'missing'])
def test_resolved_aliases_local_names_fallback_and_missing_pool(font_binary, mode, inventory):
    run(font_binary, mode, inventory)


def test_native_last_resort_entrypoints_and_enumeration_use_same_policy(final_sources):
    for number in ('0188', '0189'):
        assert 'UxrRestrictedLastResortFont(' in final_sources[number]
    assert 'FontCache::IsUxrFontFamilyAllowed(String::FromUtf8(element.family()))' in final_sources['0190']
    assert 'GetSwitchValueUTF8(sw.first)' in final_sources['0191']
