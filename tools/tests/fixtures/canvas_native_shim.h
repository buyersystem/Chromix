// Dependency shims for extracted Canvas methods, not a replacement Skia/GPU.
#include <algorithm>
#include <array>
#include <bit>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <span>
#include <string>
#include <type_traits>
#include <vector>

#define DCHECK(x) assert(x)
#define DCHECK_GE(x, y) assert((x) >= (y))
#define DCHECK_LT(x, y) assert((x) < (y))
#define DCHECK_CALLED_ON_VALID_THREAD(x) static_cast<void>(x)
struct Unreachable {
  template <class T> void operator<<(const T&) { std::abort(); }
};
#define NOTREACHED() Unreachable{}

namespace base {
template <class T> class span {
 public:
  span(T* data, size_t size) : data_(data), size_(size) {}
  template <size_t N> explicit span(std::array<T, N>& data) : span(data.data(), N) {}
  span first(size_t n) const { return subspan(0, n); }
  span last(size_t n) const { assert(n <= size_); return subspan(size_ - n, n); }
  span subspan(size_t start, size_t n) const {
    assert(start <= size_ && n <= size_ - start);
    return {data_ + start, n};
  }
  template <class U> void copy_from(const span<U>& other) const {
    assert(size_ == other.size());
    std::memcpy(data_, other.data(), size_ * sizeof(T));
  }
  T* data() const { return data_; }
  size_t size() const { return size_; }
 private:
  T* data_;
  size_t size_;
};
template <class T, size_t N> span(std::array<T, N>&) -> span<T>;
struct CheckedProduct {
  int64_t value;
  template <class T> bool IsValid() const {
    return value >= std::numeric_limits<T>::min() && value <= std::numeric_limits<T>::max();
  }
};
inline CheckedProduct CheckMul(int a, int b) { return {int64_t{a} * b}; }
inline uint32_t SafeUnsignedAbs(int value) { return static_cast<uint32_t>(value < 0 ? -int64_t{value} : value); }
template <class T> T saturated_cast(uint32_t value) {
  return static_cast<T>(std::min<uint32_t>(value, std::numeric_limits<T>::max()));
}
}  // namespace base

namespace gfx {
struct Size { int width, height; };
class Vector2d {
 public:
  Vector2d(int x, int y) : x_(x), y_(y) {}
  int x() const { return x_; }
  int y() const { return y_; }
  Vector2d operator-() const { return {-x_, -y_}; }
 private:
  int x_, y_;
};
class Rect {
 public:
  Rect() = default;
  Rect(int x, int y, int w, int h) : x_(x), y_(y), w_(std::max(0, w)), h_(std::max(0, h)) {}
  Rect(int w, int h) : Rect(0, 0, w, h) {}
  explicit Rect(Size size) : Rect(size.width, size.height) {}
  int x() const { return x_; }
  int y() const { return y_; }
  int width() const { return w_; }
  int height() const { return h_; }
  Size size() const { return {w_, h_}; }
  bool IsEmpty() const { return w_ == 0 || h_ == 0; }
  void Offset(Vector2d delta) { x_ += delta.x(); y_ += delta.y(); }
  void Intersect(const Rect& other) {
    const int x = std::max(x_, other.x_), y = std::max(y_, other.y_);
    const int64_t right = std::min(int64_t{x_} + w_, int64_t{other.x_} + other.w_);
    const int64_t bottom = std::min(int64_t{y_} + h_, int64_t{other.y_} + other.h_);
    x_ = x; y_ = y; w_ = int(std::max<int64_t>(0, right - x)); h_ = int(std::max<int64_t>(0, bottom - y));
  }
  bool Contains(const Rect& r) const {
    return r.x_ >= x_ && r.y_ >= y_ && int64_t{r.x_} + r.w_ <= int64_t{x_} + w_ &&
        int64_t{r.y_} + r.h_ <= int64_t{y_} + h_;
  }
 private:
  int x_ = 0, y_ = 0, w_ = 0, h_ = 0;
};
}  // namespace gfx

enum SkColorType { kUnknown_SkColorType, kRGBA_8888_SkColorType, kBGRA_8888_SkColorType,
                   kRGBA_F16_SkColorType, kRGBA_F32_SkColorType };
enum SkAlphaType { kUnknown_SkAlphaType, kOpaque_SkAlphaType, kPremul_SkAlphaType, kUnpremul_SkAlphaType };
inline int SkColorTypeBytesPerPixel(SkColorType type) {
  if (type == kRGBA_8888_SkColorType || type == kBGRA_8888_SkColorType) return 4;
  if (type == kRGBA_F16_SkColorType) return 8;
  if (type == kRGBA_F32_SkColorType) return 16;
  return 0;
}
struct SkIRect {
  int x, y, w, h;
  static SkIRect MakeXYWH(int x, int y, int w, int h) { return {x, y, w, h}; }
};
namespace gfx {
inline SkIRect RectToSkIRect(const Rect& r) { return {r.x(), r.y(), r.width(), r.height()}; }
}
struct SkImageInfo {
  int w = 0, h = 0;
  SkColorType ct = kRGBA_8888_SkColorType;
  SkAlphaType at = kUnpremul_SkAlphaType;
  int color_space = 23;
  int width() const { return w; }
  int height() const { return h; }
  int bytesPerPixel() const { return SkColorTypeBytesPerPixel(ct); }
  bool isEmpty() const { return w <= 0 || h <= 0; }
  size_t minRowBytes() const { return static_cast<size_t>(std::max(w, 0)) * bytesPerPixel(); }
  bool validRowBytes(size_t row) const {
    return bytesPerPixel() && row >= minRowBytes() && row % bytesPerPixel() == 0;
  }
  size_t computeByteSize(size_t row) const {
    if (isEmpty()) return 0;
    if (row && size_t(h - 1) > (SIZE_MAX - minRowBytes()) / row) return SIZE_MAX;
    const size_t size = size_t(h - 1) * row + minRowBytes();
    // Pinned SkImageInfo.cpp also caps allocations at signed 32-bit offsets.
    return size <= size_t(INT32_MAX) ? size : SIZE_MAX;
  }
  static bool ByteSizeOverflowed(size_t n) { return n == SIZE_MAX; }
  SkImageInfo makeWH(int width, int height) const { auto out = *this; out.w = width; out.h = height; return out; }
  SkImageInfo makeColorType(SkColorType type) const { auto out = *this; out.ct = type; return out; }
  SkImageInfo makeAlphaType(SkAlphaType type) const { auto out = *this; out.at = type; return out; }
};
class SkPixmap {
 public:
  SkPixmap() = default;
  SkPixmap(SkImageInfo info, const void* data, size_t row) : info_(info), data_(data), row_(row) {}
  const SkImageInfo& info() const { return info_; }
  int width() const { return info_.width(); }
  int height() const { return info_.height(); }
  SkColorType colorType() const { return info_.ct; }
  size_t rowBytes() const { return row_; }
  const void* addr() const { return data_; }
  const void* addr(int x, int y) const {
    assert(x >= 0 && y >= 0 && x < width() && y < height());
    return static_cast<const uint8_t*>(data_) + size_t(y) * row_ + size_t(x) * info_.bytesPerPixel();
  }
  void* writable_addr() const { return const_cast<void*>(data_); }
  bool extractSubset(SkPixmap* out, const SkIRect& r) const {
    const int64_t x = std::max(0, r.x), y = std::max(0, r.y);
    const int64_t right = std::min<int64_t>(width(), int64_t{r.x} + r.w);
    const int64_t bottom = std::min<int64_t>(height(), int64_t{r.y} + r.h);
    if (x >= right || y >= bottom) return false;
    *out = SkPixmap(info_.makeWH(int(right - x), int(bottom - y)), addr(int(x), int(y)), row_);
    return true;
  }
 private:
  SkImageInfo info_;
  const void* data_ = nullptr;
  size_t row_ = 0;
};
namespace gfx {
inline base::span<const uint8_t> SkPixmapToSpan(const SkPixmap& pm) {
  return {static_cast<const uint8_t*>(pm.addr()), pm.info().computeByteSize(pm.rowBytes())};
}
inline base::span<uint8_t> SkPixmapToWritableSpan(const SkPixmap& pm) {
  return {static_cast<uint8_t*>(pm.writable_addr()), pm.info().computeByteSize(pm.rowBytes())};
}
}

inline float ReadComponent(const uint8_t* source, size_t n) {
  if (n == 1) return source[0] / 255.0f;
  if (n == 2) { _Float16 value; std::memcpy(&value, source, 2); return float(value); }
  float value; std::memcpy(&value, source, 4); return value;
}
inline void WriteComponent(uint8_t* destination, size_t n, float value) {
  if (n == 1) { destination[0] = uint8_t(std::lround(std::clamp(value, 0.0f, 1.0f) * 255)); return; }
  if (n == 2) { const _Float16 half = _Float16(value); std::memcpy(destination, &half, 2); return; }
  std::memcpy(destination, &value, 4);
}
class SkBitmap {
 public:
  static inline int allocations = 0, fail_allocation = 0;
  static inline size_t padding = 0;
  static inline SkImageInfo last_allocation;
  bool tryAllocPixels(SkImageInfo info) {
    ++allocations;
    last_allocation = info;
    if (fail_allocation == allocations || info.isEmpty()) return false;
    const size_t stride = info.minRowBytes() + padding;
    assert(info.computeByteSize(stride) < 1024 * 1024);
    bytes.assign(stride * info.height(), 0xce);
    pixmap_ = SkPixmap(info, bytes.data(), stride);
    return true;
  }
  const SkPixmap& pixmap() const { return pixmap_; }
  bool writePixels(const SkPixmap& source, int x, int y) {
    assert(x == 0 && y == 0 && source.width() == pixmap_.width() && source.height() == pixmap_.height());
    const size_t in = source.info().bytesPerPixel() / 4, out = pixmap_.info().bytesPerPixel() / 4;
    for (int row = 0; row < source.height(); ++row) {
      for (int col = 0; col < source.width(); ++col) {
        auto* from = static_cast<const uint8_t*>(source.addr(col, row));
        auto* to = static_cast<uint8_t*>(pixmap_.writable_addr()) + size_t(row) * pixmap_.rowBytes() + col * out * 4;
        for (int component = 0; component < 4; ++component)
          WriteComponent(to + component * out, out, ReadComponent(from + component * in, in));
      }
    }
    return true;
  }
  std::vector<uint8_t> bytes;
 private:
  SkPixmap pixmap_;
};

enum class DOMExceptionCode { kInvalidStateError };
struct ExceptionState {
  std::string message, kind;
  void ThrowDOMException(DOMExceptionCode, const char* text) { kind = "DOM"; message = text; }
  void ThrowRangeError(const char* text) { kind = "Range"; message = text; }
};
struct ImageData {
  SkPixmap pixmap;
  bool detached = false;
  int width() const { return pixmap.width(); }
  int height() const { return pixmap.height(); }
  bool IsBufferBaseDetached() const { return detached; }
  SkPixmap GetSkPixmap() const { return pixmap; }
};
struct CanvasPerformanceMonitor { enum class DrawType { kImageData }; };
namespace viz { inline SkColorType ToClosestSkColorType(SkColorType type) { return type; } }
struct ColorParams {
  SkColorType format = kRGBA_8888_SkColorType;
  SkColorType GetSharedImageFormat() const { return format; }
};
class BaseRenderingContext2D {
 public:
  int layer_count_ = 0, w = 6, h = 5, writes = 0, draws = 0;
  bool alpha = true, lost = false, provider = true, paint = true;
  ColorParams color_params_;
  SkImageInfo written_info;
  int written_x = -1, written_y = -1;
  size_t written_stride = 0;
  gfx::Rect draw_rect;
  std::vector<uint8_t> written;
  bool HasAlpha() const { return alpha; }
  bool isContextLost() const { return lost; }
  bool CanCreateResourceProvider() const { return provider; }
  int Width() const { return w; }
  int Height() const { return h; }
  void* GetPaintCanvas() { return paint ? this : nullptr; }
  void WillDraw(gfx::Rect rect, CanvasPerformanceMonitor::DrawType) { ++draws; draw_rect = rect; }
  void WritePixels(SkImageInfo info, const void* pixels, size_t stride, int x, int y) {
    ++writes; written_info = info; written_stride = stride; written_x = x; written_y = y;
    written.resize(info.minRowBytes() * info.height());
    for (int row = 0; row < info.height(); ++row)
      std::memcpy(written.data() + size_t(row) * info.minRowBytes(),
                  static_cast<const uint8_t*>(pixels) + size_t(row) * stride, info.minRowBytes());
  }
  void putImageData(ImageData*, int, int, ExceptionState&);
  void putImageData(ImageData*, int, int, int, int, int, int, ExceptionState&);
  void PutByteArray(const SkPixmap&, const gfx::Rect&, const gfx::Vector2d&);
};

using GLuint = unsigned int;
namespace gpu::raster {
struct RasterInterface {
  SkImageInfo source_info{4, 3}, last_info;
  int calls = 0, last_x = 0, last_y = 0;
  GLuint last_stride = 0;
  void* last_pixels = nullptr;
  bool fail = false;
  static uint8_t PixelByte(int x, int y, int byte) { return uint8_t(19 + x * 11 + y * 29 + byte * 7); }
  bool ReadbackImagePixels(int mailbox, SkImageInfo info, GLuint stride,
                           int x, int y, int plane, void* pixels) {
    ++calls; last_info = info; last_x = x; last_y = y; last_stride = stride; last_pixels = pixels;
    assert(mailbox == 17 && plane == 0);
    if (fail || x < 0 || y < 0 || int64_t{x} + info.width() > source_info.width() ||
        int64_t{y} + info.height() > source_info.height()) return false;
    for (int row = 0; row < info.height(); ++row)
      for (int col = 0; col < info.width(); ++col)
        for (int byte = 0; byte < info.bytesPerPixel(); ++byte)
          static_cast<uint8_t*>(pixels)[size_t(row) * stride + col * info.bytesPerPixel() + byte] =
              PixelByte(x + col, y + row, byte);
    return true;
  }
};
}
struct RasterContextProvider {
  gpu::raster::RasterInterface raster;
  gpu::raster::RasterInterface* RasterInterface() { return &raster; }
};
class MailboxTextureBacking {
 public:
  SkImageInfo sk_image_info_{4, 3};
  RasterContextProvider provider;
  RasterContextProvider* context_provider_ = &provider;
  int thread_checker_ = 0;
  int GetMailbox() const { return 17; }
  bool readPixels(const SkImageInfo&, void*, size_t, int, int);
};
