"""Actual final audio/codec methods with bounded dependency shims, not DSP/device acceptance."""
from pathlib import Path
import shutil
import subprocess

import pytest

from test_backend_completion import EVIDENCE, backend_binary, final_sources, run
from test_fingerprint_config import config_binary
from test_fingerprint_canvas import CXX, sanitizer_flags


def method(signature):
    return EVIDENCE['methods'][signature]['text'] + '\n'


def compile_configured(directory, config_root, source):
    directory.mkdir()
    cpp = directory / 'contract.cc'
    cpp.write_text(source, encoding='utf-8')
    binary = directory / 'contract'
    result = subprocess.run([CXX, '-std=c++20', '-Wall', '-Wextra', '-Werror', *sanitizer_flags(),
        '-I', str(config_root), str(config_root / 'base/uxr_config.cc'), str(cpp), '-o', str(binary)],
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


AUDIO_SUPPORT = r'''
#include "base/uxr_config.h"
#include <bit>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <span>
#include <vector>
struct AudioChannel {
  std::vector<float> samples;
  std::span<float> MutableSpan() { return samples; }
};
struct AudioBus {
  std::vector<AudioChannel> channels;
  bool silent=false;
  bool IsSilent() const {return silent;}
  unsigned NumberOfChannels() const {return channels.size();}
  AudioChannel* Channel(unsigned i) {return &channels.at(i);}
};
bool Same(const std::vector<float>& a, const std::vector<float>& b) {
  if(a.size()!=b.size()) return false;
  for(size_t i=0;i<a.size();++i)
    if(std::bit_cast<uint32_t>(a[i])!=std::bit_cast<uint32_t>(b[i])) return false;
  return true;
}
'''

AUDIO_MAIN = r'''
int main(int argc, char** argv) {
  assert(argc>=3);
  auto& config=base::UxrConfig::GetInstance();
  base::flat_map<std::string,std::string> values={{"uxr-audio-render",argv[1]}, {"uxr-audio-seed",argv[2]}};
  if(argc>3) values["uxr-disable-fingerprint-noise"]="false";
  assert(config.SetAll(values));
  const auto seed=config.AudioIsolationSeed();
  AudioBus bus;
  std::vector<float> source={0.0f,-0.0f,std::numeric_limits<float>::denorm_min(),
    -std::numeric_limits<float>::denorm_min(),1,-1,16,-16,
    std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity(),
    std::bit_cast<float>(uint32_t{0x7fc12345})};
  for(int i=0;i<4096;++i) source.push_back(static_cast<float>(std::sin(i/37.0)*0.47));
  for(int channel=0;channel<2;++channel) bus.channels.push_back({source});
  const auto frames=source.size()-3;
  ApplyUxrAudioIsolation(&bus,frames,seed);
  const auto result=bus.channels[0].samples;
  assert(Same(result,bus.channels[1].samples));
  if(!seed) assert(Same(source,result));
  for(size_t i=0;i<source.size();++i) {
    if(!seed||source[i]==0||!std::isfinite(source[i])||std::abs(source[i])>=16||i>=frames)
      assert(std::bit_cast<uint32_t>(source[i])==std::bit_cast<uint32_t>(result[i]));
    else {
      assert(std::abs(static_cast<double>(source[i])-result[i])<=0x1.0p-20);
      assert(std::ldexp(result[i],20)==std::trunc(std::ldexp(result[i],20)));
    }
  }
  ApplyUxrAudioIsolation(&bus,frames,seed);
  assert(Same(result,bus.channels[0].samples));
  AudioBus silent;silent.silent=true;silent.channels.push_back({source});
  ApplyUxrAudioIsolation(&silent,frames,seed);assert(Same(source,silent.channels[0].samples));
  std::vector<float> partitioned;
  for(size_t offset=0;offset<frames;offset+=128) {
    size_t end=std::min(frames,offset+128);AudioBus chunk;
    chunk.channels.push_back({{source.begin()+offset,source.begin()+end}});
    ApplyUxrAudioIsolation(&chunk,end-offset,seed);
    partitioned.insert(partitioned.end(),chunk.channels[0].samples.begin(),chunk.channels[0].samples.end());
  }
  partitioned.insert(partitioned.end(),source.begin()+frames,source.end());
  assert(Same(result,partitioned));
  uint64_t hash=14695981039346656037ull;
  for(float v:result) {hash^=std::bit_cast<uint32_t>(v);hash*=1099511628211ull;}
  std::cout<<hash;
}
'''


@pytest.fixture(scope='module')
def audio_binary(tmp_path_factory, backend_binary):
    return compile_configured(tmp_path_factory.mktemp('audio-parent') / 'src', backend_binary.parent,
                              AUDIO_SUPPORT + method('void ApplyUxrAudioIsolation(') + AUDIO_MAIN)


@pytest.mark.parametrize('seed', ['1', '42', '9007199254740993', '18446744073709551615'])
def test_graph_samples_are_stable_bounded_idempotent_and_block_independent(audio_binary, seed):
    assert run(audio_binary, 'isolated', seed) == run(audio_binary, 'isolated', seed)


def test_audio_native_and_disable_paths_preserve_exact_bits(audio_binary):
    native = run(audio_binary, 'native', '42')
    assert run(audio_binary, 'isolated', '42', 'disabled') == native
    isolated = {run(audio_binary, 'isolated', str(seed)) for seed in (1, 42, 999, 65537)}
    assert len(isolated) > 1 and native not in isolated


def test_audio_is_applied_once_before_cached_fanout_not_in_getters():
    body = method('void AudioHandler::ProcessIfNecessary(')
    assert body.index('last_processing_time_ != current_time') < body.index('Process(frames_to_process);')
    assert body.index('Process(frames_to_process);') < body.index('ApplyUxrAudioIsolation(') < body.index('if (!silent_inputs)')


CODEC_SUPPORT = r'''
#include "base/uxr_config.h"
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <optional>
#include <string>
#include <vector>
struct Log { template<class T> Log& operator<<(const T&) {return *this;} };
#define DVLOG(...) Log()
#define DLOG(...) Log()
#define DCHECK(x) assert(x)
#define DCHECK_CALLED_ON_VALID_SEQUENCE(...) ((void)0)
#define TRACE_EVENT_BEGIN(...) ((void)0)
namespace media {
enum class VideoCodec {kUnknown,kH264,kVP8,kVP9,kAV1,kHEVC,kTheora};
using VideoCodecProfile=VideoCodec;
VideoCodec VideoCodecProfileToVideoCodec(VideoCodecProfile profile) {return profile;}
struct VideoType {VideoCodec codec;};
bool native_supported=true,use_client=false;
int native_calls=0;
bool IsDefaultDecoderSupportedVideoType(const VideoType&) {++native_calls;return native_supported;}
bool IsDefaultEncoderSupportedVideoType(const VideoType&) {++native_calls;return native_supported;}
struct Client {
  bool IsDecoderSupportedVideoType(const VideoType&) {++native_calls;return native_supported;}
  bool IsEncoderSupportedVideoType(const VideoType&) {++native_calls;return native_supported;}
} client;
Client* GetMediaClient() {return use_client?&client:nullptr;}
}
namespace webrtc {struct SdpVideoFormat {std::string name;};}
std::optional<media::VideoCodecProfile> WebRTCFormatToCodecProfile(const webrtc::SdpVideoFormat& format) {
  if(format.name=="H264")return media::VideoCodec::kH264;
  if(format.name=="VP8")return media::VideoCodec::kVP8;
  if(format.name=="VP9")return media::VideoCodec::kVP9;
  if(format.name=="AV1")return media::VideoCodec::kAV1;
  if(format.name=="H265")return media::VideoCodec::kHEVC;
  return {};
}
namespace media {
struct DemuxerStream {enum Type {AUDIO,VIDEO};};
struct DecoderStatus {enum class Codes{kUnsupportedConfig};};
enum class AudioCodec {kOpus};
template<DemuxerStream::Type Type> struct Config {
  using Codec=std::conditional_t<Type==DemuxerStream::VIDEO,VideoCodec,AudioCodec>;
  Codec selected;bool valid=true;
  Codec codec() const {return selected;}
  bool IsValidConfig() const {return valid;}
};
template<DemuxerStream::Type Type> struct DecoderSelector {
  using SelectDecoderCB=int;
  struct Decoder {using OutputCB=int;};
  struct Traits {
    Config<Type> config;
    Config<Type> GetDecoderConfig(int) {return config;}
  } traits;
  Traits* traits_=&traits;
  int stream_=0,select_decoder_cb_=0,output_cb_=0;
  Config<Type> config_{};
  std::optional<int> decode_failure_reinit_cause_;
  bool ran_out_of_decoders_=false;
  int created=0,initialized=0,errors=0;
  void CreateDecoders() {++created;}
  void GetAndInitializeNextDecoder() {++initialized;}
  void ReturnSelectionError(DecoderStatus::Codes) {++errors;}
  void SelectDecoderInternal(SelectDecoderCB,typename Decoder::OutputCB,bool);
};
}
'''

CODEC_MAIN = r'''
int main(int argc,char**argv) {
  auto& config=base::UxrConfig::GetInstance();base::flat_map<std::string,std::string> values;
  for(int i=1;i<argc;++i) {
    std::string arg=argv[i];const auto pos=arg.find('=');values[arg.substr(0,pos)]=arg.substr(pos+1);
  }
  assert(config.SetAll(values));
  using namespace media;
  for(auto codec:{VideoCodec::kH264,VideoCodec::kVP8,VideoCodec::kVP9,VideoCodec::kAV1,VideoCodec::kHEVC}) {
    bool allowed=IsUxrVideoCodecAllowed(codec);
    std::cout<<allowed;
    for(bool client_mode:{false,true}) for(bool supported:{false,true}) {
      use_client=client_mode;native_supported=supported;native_calls=0;
      assert(IsDecoderSupportedVideoType({codec})==(allowed&&supported));
      assert(IsEncoderSupportedVideoType({codec})==(allowed&&supported));
      assert(native_calls==(allowed?2:0));
    }
    for(bool fresh:{false,true}) {
      DecoderSelector<DemuxerStream::VIDEO> selector;selector.traits.config.selected=codec;
      selector.SelectDecoderInternal(1,2,fresh);
      assert(selector.errors==!allowed);assert(selector.initialized==allowed);
      assert(selector.created==(allowed&&fresh));
    }
  }
  DecoderSelector<DemuxerStream::AUDIO> audio;audio.traits.config.selected=AudioCodec::kOpus;
  audio.SelectDecoderInternal(1,2,true);assert(audio.errors==0&&audio.initialized==1);
  DecoderSelector<DemuxerStream::VIDEO> invalid;
  invalid.traits.config.selected=VideoCodec::kH264;invalid.traits.config.valid=false;
  invalid.SelectDecoderInternal(1,2,true);assert(invalid.errors==1&&invalid.initialized==0&&invalid.created==0);
  const auto formats=FilterUxrRtcFormats({{"H264"},{"VP8"},{"VP9"},{"AV1"},{"H265"}});
  for(const auto& format:formats) {assert(UxrRtcFormatAllowed(format));std::cout<<' '<<format.name;}
  assert(IsUxrVideoCodecAllowed(VideoCodec::kTheora));
  assert(HasUxrVideoCodecRestrictions()==(formats.size()!=5));
}
'''


@pytest.fixture(scope='module')
def codec_binary(tmp_path_factory, backend_binary):
    code = CODEC_SUPPORT + '\nnamespace media {\n'
    for signature in ('bool IsUxrVideoCodecAllowed(', 'bool HasUxrVideoCodecRestrictions()',
                      'bool IsDecoderSupportedVideoType(', 'bool IsEncoderSupportedVideoType('):
        code += method(signature)
    code += 'template<DemuxerStream::Type StreamType>\n' + method('void DecoderSelector<StreamType>::SelectDecoderInternal(')
    code += '}\n' + method('bool UxrRtcFormatAllowed(') + method('std::vector<webrtc::SdpVideoFormat> FilterUxrRtcFormats(')
    return compile_configured(tmp_path_factory.mktemp('codec-parent') / 'src', backend_binary.parent, code + CODEC_MAIN)


FAMILIES = ('h264', 'vp8', 'vp9', 'av1', 'hevc')
NAMES = ('H264', 'VP8', 'VP9', 'AV1', 'H265')


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('value', ['', 'disabled', 'native', 'supported', 'supported,smooth,power-efficient'])
def test_codec_queries_selection_reselection_and_rtc_formats_agree(codec_binary, family, value):
    allowed = [value not in ('', 'disabled') or name != family for name in FAMILIES]
    expected = ''.join(str(int(v)) for v in allowed)
    formats = [name for name, keep in zip(NAMES, allowed) if keep]
    assert run(codec_binary, f'uxr-codec-{family}={value}').split() == [expected, *formats]


def test_all_codecs_disabled_never_initializes_video_but_audio_stays_native(codec_binary):
    assert run(codec_binary, *(f'uxr-codec-{family}=disabled' for family in FAMILIES)) == '00000'


def test_native_media_result_is_authoritative_without_policy(codec_binary):
    assert run(codec_binary).split() == ['11111', *NAMES]


def test_rtc_software_and_hardware_creation_and_queries_are_both_filtered():
    for name in ('class EncoderAdapter :', 'class DecoderAdapter :'):
        body = method(name)
        assert body.count('if (!UxrRtcFormatAllowed(format))') == 2
        assert 'FilterUxrRtcFormats(' in body
        assert body.index('if (!UxrRtcFormatAllowed(format))') < body.index('supported_in_hardware' if 'Encoder' in name else 'software_decoder =')


RECORDER_SUPPORT = r'''
#include "base/uxr_config.h"
#include <algorithm>
#include <cassert>
#include <iostream>
#include <span>
#include <string>
#include <vector>
#define BUILDFLAG(name) name
#define USE_PROPRIETARY_CODECS 1
#define ENABLE_HEVC_PARSER_AND_HW_DECODER 1
struct Log { template<class T> Log& operator<<(const T&) {return *this;} };
#define DVLOG(...) Log()
namespace base {using std::span; template<class T> using raw_span=std::span<T>;}
namespace media {
enum class VideoCodec {kUnknown,kH264,kVP8,kVP9,kAV1,kHEVC};
enum VideoCodecProfile {H264PROFILE_BASELINE=10,H264PROFILE_HIGH=12,VP8PROFILE_ANY=20,
  VP9PROFILE_PROFILE0=30,AV1PROFILE_PROFILE_MAIN=40,HEVCPROFILE_MAIN=50};
const char* GetProfileName(VideoCodecProfile) {return "invented profile";}
}
enum class MediaTrackContainerType {kVideoWebM,kVideoMp4,kVideoMatroska,kAudioMp4};
struct Size {std::string ToString() const {return "invented size";}};
struct Profile {
  media::VideoCodecProfile profile;
  Size min_resolution{},max_resolution{};
  int max_framerate_numerator=30,max_framerate_denominator=1;
};
std::vector<Profile> hardware;
unsigned software=0;
auto GetVEASupportedProfiles() {return hardware;}
bool IsSoftwareEncoderAvailable(media::VideoCodec c) {return software&(1u<<static_cast<int>(c));}
struct VideoTrackRecorderImpl {
  static media::VideoCodec GetPreferredCodec(MediaTrackContainerType);
};
'''

RECORDER_MAIN = r'''
int main(int argc,char**argv) {
  assert(argc>=3);
  const unsigned hw=std::stoul(argv[1]);software=std::stoul(argv[2]);
  base::flat_map<std::string,std::string> values;
  for(int i=3;i<argc;++i) values[std::string("uxr-codec-")+argv[i]]="disabled";
  assert(base::UxrConfig::GetInstance().SetAll(values));
  // Invented hardware order deliberately differs from the preference table.
  for(auto profile:{media::H264PROFILE_BASELINE,media::HEVCPROFILE_MAIN,
                    media::VP9PROFILE_PROFILE0,media::VP8PROFILE_ANY,media::AV1PROFILE_PROFILE_MAIN})
    if(hw&(1u<<(static_cast<int>(profile)/10))) hardware.push_back({profile});
  for(auto type:{MediaTrackContainerType::kVideoWebM,MediaTrackContainerType::kVideoMp4,
                 MediaTrackContainerType::kVideoMatroska,MediaTrackContainerType::kAudioMp4})
    std::cout<<static_cast<int>(VideoTrackRecorderImpl::GetPreferredCodec(type))<<' ';
}
'''


@pytest.fixture(scope='module')
def recorder_binary(tmp_path_factory, backend_binary):
    code = RECORDER_SUPPORT + '\nnamespace media {\n' + method('bool IsUxrVideoCodecAllowed(') + '}\n'
    code += method('native MediaRecorder codec table')
    code += method('media::VideoCodec VideoTrackRecorderImpl::GetPreferredCodec(') + RECORDER_MAIN
    return compile_configured(tmp_path_factory.mktemp('recorder-parent') / 'src', backend_binary.parent, code)


@pytest.mark.parametrize('disabled', [(), ('vp8',), ('vp8','vp9'), ('vp8','vp9','av1'),
    ('h264','vp9','hevc'), FAMILIES])
@pytest.mark.parametrize('hw,sw', [(0,30), (0,0), (62,30), (34,30), (0,2)])
def test_recorder_uses_an_allowed_installed_encoder_for_its_container(recorder_binary, disabled, hw, sw):
    # Independent expectation from the invented inventory, not its own saved status.
    numbers = {'h264':1, 'vp8':2, 'vp9':3, 'av1':4, 'hevc':5}
    expected = []
    for container, video in (('webm',True), ('mp4',True), ('matroska',True), ('mp4',False)):
        suitable = {'vp8','vp9','av1'} if container == 'webm' else (
            {'h264','vp9','av1','hevc'} if container == 'mp4' else set(FAMILIES))
        allowed = suitable - set(disabled)
        hardware = [c for c in ('h264','hevc','vp9','vp8','av1')
                    if video and c in allowed and hw & (1 << numbers[c])]
        software = [c for c in ('vp8','vp9','av1','h264') if c in allowed and sw & (1 << numbers[c])]
        choices = hardware or software
        expected.append(str(numbers[choices[0]] if choices else 0))
    assert run(recorder_binary, str(hw), str(sw), *disabled).split() == expected
