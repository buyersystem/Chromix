int main(int argc,char**argv){
  assert(argc==4);
  std::string name=argv[1];
  auto transport=std::make_unique<StreamSocket>();auto* io=transport.get();
  io->mode=std::stoi(argv[2]);io->chunk=std::stoi(argv[3]);
  std::optional<SOCKS5ClientSocket::Credentials> creds=SOCKS5ClientSocket::Credentials{"user","pass"};
  if(name=="noauth")creds.reset();
  if(name=="empty-user")creds->username="";
  if(name=="empty-pass")creds->password="";
  if(name=="long-user")creds->username=std::string(256,'u');
  if(name=="long-pass")creds->password=std::string(256,'p');
  if(name=="max-auth")creds=SOCKS5ClientSocket::Credentials{std::string(255,'u'),std::string(255,'p')};
  io->incoming={5,static_cast<uint8_t>(creds?2:0)};
  if(creds){io->incoming.push_back(1);io->incoming.push_back(0);}
  io->incoming.insert(io->incoming.end(),{5,0,0,1,127,0,0,1,0,80});
  if(name=="downgrade")io->incoming[1]=0;
  if(name=="unknown-method")io->incoming[1]=255;
  if(name=="auth-rejected")io->incoming[3]=1;
  if(name=="bad-auth-version")io->incoming[2]=5;
  if(name=="auth-eof")io->incoming.resize(3);
  if(name=="zero-write")io->zero_auth_write=true;
  if(name=="write-error")io->error_auth_write=true;
  std::string host=name=="long-host"?std::string(256,'h'):"localhost";
  SOCKS5ClientSocket socket(std::move(transport),HostPortPair(host,80),creds);
  int calls=0,result=999;
  int rc=socket.Connect(CompletionOnceCallback([&](int n){++calls;result=n;}));
  if(rc==ERR_IO_PENDING){int steps=0;while(io->pending){assert(++steps<4096);io->pump();}assert(calls==1);rc=result;}
  else assert(calls==0);
  std::cout<<rc<<" "<<socket.completed_handshake_<<" ";
  constexpr char hex[]="0123456789abcdef";
  for(uint8_t byte:io->outgoing)std::cout<<hex[byte>>4]<<hex[byte&15];
  std::cout<<"\n";
  if(rc==OK){assert(socket.Connect({})==OK);assert(io->read_offset==io->incoming.size());}
  socket.Disconnect();assert(!io->connected&&!io->pending);
}
