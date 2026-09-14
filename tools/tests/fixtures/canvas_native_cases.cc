// Independent byte/geometry oracles for the extracted methods.
const std::array<SkColorType, 4> kFormats = {kRGBA_8888_SkColorType,
    kBGRA_8888_SkColorType, kRGBA_F16_SkColorType, kRGBA_F32_SkColorType};

struct Input {
  SkImageInfo info;
  size_t stride;
  std::vector<uint8_t> bytes;
  ImageData image;
  explicit Input(SkColorType type = kRGBA_8888_SkColorType, size_t padding = 0)
      : info{5, 4, type}, stride(info.minRowBytes() + padding),
        bytes(stride * info.height() + 32, 0xbd), image{SkPixmap(info, bytes.data(), stride)} {
    const size_t component = info.bytesPerPixel() / 4;
    for (int y = 0; y < info.height(); ++y) for (int x = 0; x < info.width(); ++x) {
      auto* pixel = bytes.data() + size_t(y) * stride + x * component * 4;
      WriteComponent(pixel, component, float(x + 1) / 8);
      WriteComponent(pixel + component, component, float(y + 1) / 8);
      WriteComponent(pixel + component * 2, component, 0.625f);
      WriteComponent(pixel + component * 3, component, float((x + y) % 4) / 4);
    }
  }
};

void ResetAllocations() {
  SkBitmap::allocations = SkBitmap::fail_allocation = 0;
  SkBitmap::padding = 0;
}

void CheckUpload(SkColorType type, bool alpha, int dx = 0, int dy = 0,
                 int dirty_x = 0, int dirty_y = 0, int dirty_w = 5, int dirty_h = 4,
                 SkColorType destination = kUnknown_SkColorType, bool paint = true) {
  ResetAllocations();
  Input input(type, 16);
  const auto original = input.bytes;
  BaseRenderingContext2D canvas;
  canvas.alpha = alpha; canvas.paint = paint;
  canvas.color_params_.format = destination == kUnknown_SkColorType ? type : destination;
  ExceptionState error;
  canvas.putImageData(&input.image, dx, dy, dirty_x, dirty_y, dirty_w, dirty_h, error);
  assert(error.message.empty() && input.bytes == original);
  // Build the expected written pixels by membership, not by the implementation's
  // offset/intersection/rebasing sequence.
  if (dirty_w < 0) { dirty_x += dirty_w; dirty_w = -dirty_w; }
  if (dirty_h < 0) { dirty_y += dirty_h; dirty_h = -dirty_h; }
  std::vector<std::array<int, 2>> selected;
  for (int y = 0; y < input.info.height(); ++y) for (int x = 0; x < input.info.width(); ++x) {
    if (x >= dirty_x && y >= dirty_y && x < dirty_x + dirty_w && y < dirty_y + dirty_h &&
        x + dx >= 0 && y + dy >= 0 && x + dx < canvas.w && y + dy < canvas.h)
      selected.push_back({x, y});
  }
  if (selected.empty()) {
    assert(canvas.writes == 0 && canvas.draws == 0 && SkBitmap::allocations == 0);
    return;
  }
  assert(canvas.writes == 1 && canvas.draws == (paint ? 1 : 0));
  assert(canvas.written_x == selected.front()[0] + dx && canvas.written_y == selected.front()[1] + dy);
  const int width = selected.back()[0] - selected.front()[0] + 1;
  const int height = selected.back()[1] - selected.front()[1] + 1;
  assert(canvas.written_info.width() == width && canvas.written_info.height() == height);
  assert(canvas.written_info.at == (alpha ? kUnpremul_SkAlphaType : kOpaque_SkAlphaType));
  assert(canvas.written_info.color_space == input.info.color_space);
  if (paint) {
    assert(canvas.draw_rect.x() == canvas.written_x && canvas.draw_rect.y() == canvas.written_y);
    assert(canvas.draw_rect.width() == width && canvas.draw_rect.height() == height);
  }
  const size_t src = input.info.bytesPerPixel() / 4, dst = canvas.written_info.bytesPerPixel() / 4;
  assert(canvas.written.size() == selected.size() * dst * 4);
  for (size_t pixel = 0; pixel < selected.size(); ++pixel) {
    auto* from = input.bytes.data() + size_t(selected[pixel][1]) * input.stride + selected[pixel][0] * src * 4;
    auto* to = canvas.written.data() + pixel * dst * 4;
    for (size_t channel = 0; channel < 4; ++channel) {
      if (!alpha && channel == 3) {
        assert(ReadComponent(to + channel * dst, dst) == 1.0f);
      } else if (src == dst) {
        assert(std::memcmp(from + channel * src, to + channel * dst, src) == 0);
      } else {
        assert(std::abs(ReadComponent(from + channel * src, src) - ReadComponent(to + channel * dst, dst)) < 0.005f);
      }
    }
  }
  const bool converted = type != canvas.color_params_.format;
  assert(SkBitmap::allocations == int(!alpha) + int(converted));
  if (!alpha) {
    assert(SkBitmap::last_allocation.width() == width && SkBitmap::last_allocation.height() == height);
  }
}

#if CHROMIX_PATCHED
void CheckHelper(SkColorType type, bool raw_bits, size_t source_padding, size_t copy_padding) {
  ResetAllocations();
  SkBitmap::padding = copy_padding;
  Input input(type, source_padding);
  if (raw_bits) {
    for (size_t i = 0; i < input.bytes.size(); ++i) input.bytes[i] = uint8_t(i * 37 + 19);
  }
  const auto original = input.bytes;
  SkBitmap copy;
  assert(UxrCopyOpaqueImageData(input.image.pixmap, gfx::Rect(1, 1, 3, 2), copy));
  assert(input.bytes == original && SkBitmap::allocations == 1);
  const auto& pm = copy.pixmap();
  assert(pm.width() == 3 && pm.height() == 2);
  assert(pm.info().ct == type && pm.info().at == input.info.at && pm.info().color_space == input.info.color_space);
  const size_t n = input.info.bytesPerPixel() / 4;
  std::array<uint8_t, 4> one{};
  WriteComponent(one.data(), n, 1.0f);
  for (int y = 0; y < pm.height(); ++y) {
    for (int x = 0; x < pm.width(); ++x) {
      auto* src = input.bytes.data() + size_t(y + 1) * input.stride + (x + 1) * n * 4;
      auto* dst = copy.bytes.data() + size_t(y) * pm.rowBytes() + x * n * 4;
      assert(std::memcmp(src, dst, n * 3) == 0);
      assert(std::memcmp(one.data(), dst + n * 3, n) == 0);
    }
    for (size_t p = pm.info().minRowBytes(); p < pm.rowBytes(); ++p)
      assert(copy.bytes[size_t(y) * pm.rowBytes() + p] == 0xce);
  }
}
#endif

void CheckMailbox(int sx, int sy, int width, int height, size_t padding = 0,
                  SkColorType type = kRGBA_8888_SkColorType) {
  MailboxTextureBacking image;
  image.sk_image_info_.ct = type; image.provider.raster.source_info = image.sk_image_info_;
  const SkImageInfo info{width, height, type, kPremul_SkAlphaType, 29};
  const size_t stride = info.minRowBytes() + padding, prefix = 32;
  std::vector<uint8_t> storage(prefix + stride * height + 32, 0xbd), expected = storage;
  bool intersects = false;
  int first_x = -1, first_y = -1;
  for (int y = 0; y < height; ++y) for (int x = 0; x < width; ++x) {
    const int64_t px = int64_t{sx} + x, py = int64_t{sy} + y;
    if (px < 0 || py < 0 || px >= image.sk_image_info_.width() || py >= image.sk_image_info_.height()) continue;
    if (!intersects) { first_x = x; first_y = y; }
    intersects = true;
    for (int c = 0; c < info.bytesPerPixel(); ++c)
      expected[prefix + size_t(y) * stride + x * info.bytesPerPixel() + c] =
          gpu::raster::RasterInterface::PixelByte(int(px), int(py), c);
  }
  const bool success = image.readPixels(info, storage.data() + prefix, stride, sx, sy);
  assert(success == intersects && storage == expected);
  auto& gpu = image.provider.raster;
  assert(gpu.calls == int(intersects));
  if (intersects) {
    assert(gpu.last_stride == stride);
    assert(gpu.last_x == sx + first_x && gpu.last_y == sy + first_y);
    assert(gpu.last_pixels == storage.data() + prefix + size_t(first_y) * stride + first_x * info.bytesPerPixel());
    assert(gpu.last_info.color_space == info.color_space && gpu.last_info.at == info.at && gpu.last_info.ct == info.ct);
    assert(gpu.last_info.width() == std::min(width - first_x, image.sk_image_info_.width() - gpu.last_x));
    assert(gpu.last_info.height() == std::min(height - first_y, image.sk_image_info_.height() - gpu.last_y));
  }
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string test = argv[1];
  ResetAllocations();
  if (test == "alpha-true" || test == "no-paint") {
    for (auto type : kFormats) CheckUpload(type, true, -1, 1, 0, 0, 5, 4, kUnknown_SkColorType, test != "no-paint");
  } else if (test == "empty-dirty") {
    CheckUpload(kRGBA_8888_SkColorType, true, 0, 0, 0, 0, 0, 3);
    CheckUpload(kRGBA_8888_SkColorType, true, 20, 20);
    CheckUpload(kRGBA_8888_SkColorType, true, 0, 0, 10, 10, 2, 2);
  } else if (test == "conversion-alpha-true") {
    for (auto type : kFormats) for (auto to : kFormats)
      if (type != kBGRA_8888_SkColorType && to != kBGRA_8888_SkColorType)
        CheckUpload(type, true, -1, 1, 1, 0, 4, 4, to);
  } else if (test == "detached" || test == "open-layer" || test == "lost-context" || test == "no-provider") {
    Input input; BaseRenderingContext2D canvas; ExceptionState error;
    input.image.detached = test == "detached"; canvas.layer_count_ = test == "open-layer";
    canvas.lost = test == "lost-context"; canvas.provider = test != "no-provider";
    canvas.putImageData(&input.image, 0, 0, error);
    assert(canvas.writes == 0 && canvas.draws == 0 && SkBitmap::allocations == 0);
    assert((error.kind == "DOM") == (test == "detached" || test == "open-layer"));
  } else if (test == "mailbox-inbounds") {
    CheckMailbox(0, 0, 4, 3); CheckMailbox(1, 1, 2, 2);
  } else if (test == "mailbox-error") {
    MailboxTextureBacking image; image.provider.raster.fail = true;
    std::array<uint8_t, 16> bytes{};
    assert(!image.readPixels(SkImageInfo{2, 2}, bytes.data(), 8, 0, 0));
    assert(image.provider.raster.calls == 1);
    assert(std::all_of(bytes.begin(), bytes.end(), [](uint8_t b) { return b == 0; }));
#if CHROMIX_PATCHED
  } else if (test == "rgba8" || test == "bgra8" || test == "f16" || test == "f32") {
    const SkColorType type = test == "rgba8" ? kRGBA_8888_SkColorType : test == "bgra8" ? kBGRA_8888_SkColorType :
        test == "f16" ? kRGBA_F16_SkColorType : kRGBA_F32_SkColorType;
    CheckHelper(type, false, 0, 0); CheckUpload(type, false);
  } else if (test == "source-bits" || test == "padding") {
    for (auto type : kFormats) CheckHelper(type, test == "source-bits", 16, 32);
  } else if (test == "allocation-failure" || test == "conversion-allocation-failure") {
    Input input; const auto original = input.bytes;
    BaseRenderingContext2D canvas; canvas.alpha = false; ExceptionState error;
    if (test == "conversion-allocation-failure") canvas.color_params_.format = kRGBA_F16_SkColorType;
    SkBitmap::fail_allocation = test == "allocation-failure" ? 1 : 2;
    canvas.putImageData(&input.image, 0, 0, error);
    assert(error.kind == "Range" && error.message == "Out of memory in putImageData");
    assert(canvas.writes == 0 && canvas.draws == 0 && input.bytes == original);
    assert(SkBitmap::allocations == SkBitmap::fail_allocation);
  } else if (test == "subset-failure" || test == "unsupported-format") {
    Input input; SkBitmap copy;
    if (test == "unsupported-format") input.image.pixmap = SkPixmap(input.info.makeColorType(kUnknown_SkColorType), input.bytes.data(), input.stride);
    assert(!UxrCopyOpaqueImageData(input.image.pixmap, test == "subset-failure" ? gfx::Rect(10, 10, 1, 1) : gfx::Rect(1, 1, 1, 1), copy));
    assert(SkBitmap::allocations == 0);
  } else if (test == "dirty-rectangle" || test == "negative-destination" || test == "negative-dirty-size") {
    for (auto type : kFormats) {
      if (test == "dirty-rectangle") CheckUpload(type, false, 3, 3, 1, 1, 8, 7);
      if (test == "negative-destination") CheckUpload(type, false, -2, -1, 1, 0, 4, 4);
      if (test == "negative-dirty-size") CheckUpload(type, false, 0, 0, 4, 3, -3, -2);
    }
  } else if (test == "conversion-opaque") {
    for (auto type : kFormats) for (auto to : kFormats)
      if (type != kBGRA_8888_SkColorType && to != kBGRA_8888_SkColorType)
        CheckUpload(type, false, -1, 1, 1, 0, 4, 4, to);
  } else if (test == "opaque-random") {
    uint32_t seed = 123;
    auto next = [&] { seed = seed * 1664525u + 1013904223u; return seed; };
    for (int i = 0; i < 400; ++i) {
      const int dx = int(next() % 13) - 6, dy = int(next() % 11) - 5;
      const int x = int(next() % 9) - 2, y = int(next() % 8) - 2;
      const int w = int(next() % 8), h = int(next() % 7);
      CheckUpload(kFormats[next() % 4], false, dx, dy, x, y, w, h);
    }
  } else if (test == "mailbox-padding") {
    for (auto type : kFormats) CheckMailbox(0, 0, 4, 3, 32, type);
  } else if (test == "mailbox-oob" || test == "mailbox-formats") {
    for (auto type : kFormats) for (int sy = -4; sy <= 4; ++sy) for (int sx = -5; sx <= 5; ++sx)
      CheckMailbox(sx, sy, 6, 5, test == "mailbox-formats" ? 32 : 0, type);
  } else if (test == "mailbox-random") {
    uint32_t seed = 79;
    auto next = [&] { seed = seed * 1664525u + 1013904223u; return seed; };
    for (int i = 0; i < 800; ++i) {
      const int sx = int(next() % 17) - 8, sy = int(next() % 15) - 7;
      const int w = int(next() % 10) + 1, h = int(next() % 9) + 1;
      CheckMailbox(sx, sy, w, h, 32, kFormats[next() % 4]);
    }
  } else if (test == "mailbox-invalid") {
    MailboxTextureBacking image; std::array<uint8_t, 32> bytes{};
    assert(!image.readPixels(SkImageInfo{1, 1}, nullptr, 4, 0, 0));
    for (auto info : {SkImageInfo{0, 1}, SkImageInfo{1, 0}, SkImageInfo{-1, 1}, SkImageInfo{1, 1, kUnknown_SkColorType}})
      assert(!image.readPixels(info, bytes.data(), 4, 0, 0));
    assert(!image.readPixels(SkImageInfo{2, 1}, bytes.data(), 4, 0, 0));
    assert(!image.readPixels(SkImageInfo{1, 1}, bytes.data(), 5, 0, 0));
    assert(!image.readPixels(SkImageInfo{1, 1}, bytes.data(), size_t{UINT32_MAX} + 1, 0, 0));
    assert(!image.readPixels(SkImageInfo{1, INT32_MAX}, bytes.data(), 8, 0, 0));
    image.sk_image_info_.w = 0;
    assert(!image.readPixels(SkImageInfo{1, 1}, bytes.data(), 4, 0, 0));
    assert(image.provider.raster.calls == 0);
  } else if (test == "mailbox-extreme") {
    for (int x : {INT32_MIN, INT32_MAX, INT32_MIN + 1, INT32_MAX - 1}) {
      CheckMailbox(x, 0, 2, 2, 16); CheckMailbox(0, x, 2, 2, 16);
    }
#else
  } else if (test == "native-regression") {
    Input input; BaseRenderingContext2D canvas; canvas.alpha = false; ExceptionState error;
    canvas.putImageData(&input.image, 0, 0, error);
    assert(canvas.written_info.at == kOpaque_SkAlphaType && canvas.written[3] != 255);
    assert(SkBitmap::allocations == 0);
    MailboxTextureBacking image; std::array<uint8_t, 64> output{};
    assert(image.readPixels(SkImageInfo{2, 2}, output.data(), 16, 0, 0));
    assert(image.provider.raster.last_stride == 8);  // Caller stride was ignored.
    assert(!image.readPixels(SkImageInfo{2, 2}, output.data(), 8, -1, 0));
    assert(image.provider.raster.last_x == -1);  // OOB was forwarded without clipping.
#endif
  } else {
    assert(false && "unknown test case");
  }
  std::cout << "PASS " << test << '\n';
}
