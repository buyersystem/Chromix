// Test-only byte transport and async scheduler. Not a Chromium network backend.
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <functional>
#include <iostream>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>
#define DCHECK(x) assert(x)
#define DCHECK_EQ(a,b) assert((a)==(b))
#define DCHECK_NE(a,b) assert((a)!=(b))
#define NOTREACHED() (assert(false), std::cerr)
constexpr int OK=0, ERR_IO_PENDING=-1, ERR_INVALID_ARGUMENT=-4;
constexpr int ERR_SOCKS_CONNECTION_FAILED=-120, ERR_CONNECTION_CLOSED=-100;
struct in_addr { uint8_t bytes[4]; };
struct in6_addr { uint8_t bytes[16]; };
template<class T> using scoped_refptr=std::shared_ptr<T>;
namespace base {
template<class T,size_t N=std::dynamic_extent> using span=std::span<T,N>;
template<class T,class...Args> auto MakeRefCounted(Args&&...args){return std::make_shared<T>(std::forward<Args>(args)...);}
template<class T,class V> T checked_cast(V v){assert(v<=255);return static_cast<T>(v);}
inline auto U16ToBigEndian(uint16_t v){return std::array<uint8_t,2>{static_cast<uint8_t>(v>>8),static_cast<uint8_t>(v)};}
}
struct CompletionOnceCallback {
  std::function<void(int)> fn;
  CompletionOnceCallback()=default;
  CompletionOnceCallback(std::function<void(int)> f):fn(std::move(f)){}
  bool is_null()const{return !fn;}
  void Reset(){fn={};}
  void Run(int r){auto copy=std::move(fn);fn={};copy(r);}
};
using CompletionRepeatingCallback=std::function<void(int)>;
struct NetworkTrafficAnnotationTag {};
struct IOBuffer {
  virtual ~IOBuffer()=default;
  virtual uint8_t* data()=0;
  virtual int size()const=0;
};
struct VectorIOBuffer:IOBuffer {
  std::vector<uint8_t> bytes;
  explicit VectorIOBuffer(std::vector<uint8_t> v):bytes(std::move(v)){}
  uint8_t* data()override{return bytes.data();}
  int size()const override{return static_cast<int>(bytes.size());}
};
struct WrappedIOBuffer:VectorIOBuffer {
  template<class T> explicit WrappedIOBuffer(const T& v):VectorIOBuffer({v.begin(),v.end()}){}
};
struct DrainableIOBuffer:IOBuffer {
  std::shared_ptr<IOBuffer> parent;int length,offset=0;
  DrainableIOBuffer(std::shared_ptr<IOBuffer> p,int n):parent(std::move(p)),length(n){}
  uint8_t* data()override{return parent->data()+offset;}
  int size()const override{return BytesRemaining();}
  int BytesRemaining()const{return length-offset;}
  void DidConsume(int n){assert(n>=0&&n<=BytesRemaining());offset+=n;}
};
struct GrowableIOBuffer:IOBuffer {
  std::vector<uint8_t> bytes;int used=0;
  uint8_t* data()override{return bytes.data()+used;}
  int size()const override{return RemainingCapacity();}
  void SetCapacity(size_t n){assert(n>=static_cast<size_t>(used));bytes.resize(n);}
  int RemainingCapacity()const{return static_cast<int>(bytes.size())-used;}
  int offset()const{return used;}
  void set_offset(int n){assert(n>=used&&n<=static_cast<int>(bytes.size()));used=n;}
  std::span<uint8_t> span_before_offset(){return std::span(bytes).first(used);}
};
struct HostPortPair {
  std::string name;uint16_t number;
  HostPortPair(std::string host,uint16_t port):name(std::move(host)),number(port){}
  const std::string& host()const{return name;}
  uint16_t port()const{return number;}
};
// ENUMS injected here.
struct NetLogWithSource {
  template<class...T> void BeginEvent(T&&...){}
  template<class...T> void EndEvent(T&&...){}
  template<class...T> void EndEventWithNetErrorCode(T&&...){}
  template<class...T> void AddEvent(T&&...){}
  template<class...T> void AddEventWithIntParams(T&&...){}
};
struct StreamSocket {
  std::vector<uint8_t> incoming,outgoing;
  size_t read_offset=0;int chunk=999,mode=0,io_count=0;
  bool connected=true,zero_auth_write=false,error_auth_write=false;
  std::function<void()> pending;
  int complete(std::function<void()> operation,int n,CompletionRepeatingCallback cb){
    ++io_count;
    if(mode==1||(mode==2&&io_count%2)){
      assert(!pending);pending=[operation=std::move(operation),n,cb=std::move(cb)]{operation();cb(n);};
      return ERR_IO_PENDING;
    }
    operation();return n;
  }
  int Write(IOBuffer* buffer,int len,CompletionRepeatingCallback cb,NetworkTrafficAnnotationTag){
    if(outgoing.size()==3&&zero_auth_write)return 0;
    if(outgoing.size()==3&&error_auth_write)return ERR_CONNECTION_CLOSED;
    int n=std::min(chunk,len);
    return complete([=,this]{outgoing.insert(outgoing.end(),buffer->data(),buffer->data()+n);},n,std::move(cb));
  }
  int Read(IOBuffer* buffer,int len,CompletionRepeatingCallback cb){
    int n=std::min({chunk,len,static_cast<int>(incoming.size()-read_offset)});
    return complete([=,this]{if(n)std::memcpy(buffer->data(),incoming.data()+read_offset,n);read_offset+=n;},n,std::move(cb));
  }
  void Disconnect(){connected=false;pending={};}
  void pump(){assert(pending);auto fn=std::move(pending);pending={};fn();}
};
struct SOCKS5ClientSocket {
  struct Credentials {std::string username,password;};
  enum State {STATE_GREET_WRITE,STATE_GREET_WRITE_COMPLETE,STATE_GREET_READ,STATE_GREET_READ_COMPLETE,
    STATE_AUTH_WRITE,STATE_AUTH_WRITE_COMPLETE,STATE_AUTH_READ,STATE_AUTH_READ_COMPLETE,
    STATE_HANDSHAKE_WRITE,STATE_HANDSHAKE_WRITE_COMPLETE,STATE_HANDSHAKE_READ,STATE_HANDSHAKE_READ_COMPLETE,STATE_NONE};
  enum SocksEndPointAddressType {kEndPointDomain=3,kEndPointResolvedIPv4=1,kEndPointResolvedIPv6=4};
  static const unsigned int kGreetReadHeaderSize,kWriteHeaderSize,kReadHeaderSize;
  static const uint8_t kSOCKS5Version,kTunnelCommand,kNullByte;
  std::unique_ptr<StreamSocket> transport_socket_;
  CompletionRepeatingCallback io_callback_;
  State next_state_=STATE_NONE;
  CompletionOnceCallback user_callback_;
  std::shared_ptr<DrainableIOBuffer> write_buf_;
  std::shared_ptr<GrowableIOBuffer> read_buf_;
  bool completed_handshake_=false;
  HostPortPair destination_;
  std::optional<Credentials> credentials_;
  NetLogWithSource net_log_;
  NetworkTrafficAnnotationTag traffic_annotation_;
  SOCKS5ClientSocket(std::unique_ptr<StreamSocket> transport,HostPortPair dest,std::optional<Credentials> auth)
    :transport_socket_(std::move(transport)),io_callback_([this](int n){OnIOComplete(n);}),destination_(std::move(dest)),credentials_(std::move(auth)){}
  int Connect(CompletionOnceCallback);void Disconnect();
  void DoCallback(int);void OnIOComplete(int);int DoLoop(int);
  int DoGreetWrite();int DoGreetWriteComplete(int);int DoGreetRead();int DoGreetReadComplete(int);
  int DoAuthWrite();int DoAuthWriteComplete(int);int DoAuthRead();int DoAuthReadComplete(int);
  int DoHandshakeWrite();int DoHandshakeWriteComplete(int);int DoHandshakeRead();int DoHandshakeReadComplete(int);
  std::shared_ptr<DrainableIOBuffer> BuildAuthWriteBuffer()const;
  std::shared_ptr<DrainableIOBuffer> BuildHandshakeWriteBuffer()const;
};
