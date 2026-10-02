#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <future>
#include <iostream>
#include <map>
#include <stdexcept>
#include <opencv2/dnn.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/videoio.hpp>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <windows.h>
#include <wininet.h>

namespace {
struct RectNorm { double x=0,y=0,w=0,h=0; };
struct Config {
  std::string gate_id,parking_id,gate_name,gate_type="entry",input_mode="stream",input_path;
  std::string entry_side="bottom",roi_json,worker_url,parking_api_key,client_token,barrier_url,config_path;
  std::string vehicle_model_path,plate_model_path,state_path,preview_path;
  double line_ratio=.10,track_iou_threshold=.15,vehicle_confidence=.35,vehicle_nms_threshold=.70,plate_confidence=0,vehicle_roi_percent=.50;
  int vehicle_detection_interval=10,vehicle_exit_frames=20,vehicle_stationary_frames=50;
  int plate_detection_interval=10,plate_dwell_frames=10;
};
struct Detection { cv::Rect box; int class_id=-1; float confidence=0; std::string label; cv::Mat crop; };
struct Track { cv::Rect box; cv::Point2f center; std::string phase="waiting",label; int entry_touch_frame=-1; bool opposite_touched=false; };
struct Letterbox { cv::Mat image; float scale=1; int pad_x=0,pad_y=0; };

class Args {
 public:
  Args(int argc,char** argv){for(int i=1;i+1<argc;i+=2) values_[argv[i]]=argv[i+1];}
  std::string get(const std::string& key,const std::string& fallback="")const{
    auto it=values_.find(key); return it==values_.end()?fallback:it->second;
  }
 private: std::map<std::string,std::string> values_;
};

RectNorm parse_roi(const std::string& text){
  auto read=[&](const std::string& key){
    std::string token="\""+key+"\""; auto pos=text.find(token);
    if(pos==std::string::npos){token=key;pos=text.find(token);}
    if(pos==std::string::npos)return 0.0;
    pos=text.find(':',pos+token.size());
    if(pos==std::string::npos)return 0.0;
    ++pos;
    while(pos<text.size()&&std::isspace(static_cast<unsigned char>(text[pos])))++pos;
    return std::strtod(text.c_str()+pos,nullptr);
  };
  return {std::clamp(read("x"),0.0,1.0),std::clamp(read("y"),0.0,1.0),
          std::clamp(read("w"),0.0,1.0),std::clamp(read("h"),0.0,1.0)};
}
cv::Rect roi_pixels(const RectNorm& r,const cv::Size& s){
  return cv::Rect(int(r.x*s.width),int(r.y*s.height),int(r.w*s.width),int(r.h*s.height))&cv::Rect(0,0,s.width,s.height);
}
Letterbox letterbox(const cv::Mat& frame,int width,int height){
  Letterbox out; out.scale=std::min(float(width)/frame.cols,float(height)/frame.rows);
  int rw=std::max(1,int(std::round(frame.cols*out.scale))),rh=std::max(1,int(std::round(frame.rows*out.scale)));
  out.pad_x=(width-rw)/2; out.pad_y=(height-rh)/2;
  out.image=cv::Mat(height,width,frame.type(),cv::Scalar(114,114,114));
  cv::Mat resized; cv::resize(frame,resized,{rw,rh}); resized.copyTo(out.image({out.pad_x,out.pad_y,rw,rh})); return out;
}
cv::Rect restore(float x1,float y1,float x2,float y2,const Letterbox& prep,const cv::Size& size){
  int l=std::clamp(int((x1-prep.pad_x)/prep.scale),0,size.width),t=std::clamp(int((y1-prep.pad_y)/prep.scale),0,size.height);
  int r=std::clamp(int((x2-prep.pad_x)/prep.scale),0,size.width),b=std::clamp(int((y2-prep.pad_y)/prep.scale),0,size.height);
  return {l,t,std::max(0,r-l),std::max(0,b-t)};
}
double iou(const cv::Rect&a,const cv::Rect&b){cv::Rect i=a&b;double ia=std::max(0,i.area()),u=a.area()+b.area()-ia;return u?ia/u:0;}
double inside_ratio(const cv::Rect&box,const cv::Rect&roi){cv::Rect overlap=box&roi;return box.area()>0?double(std::max(0,overlap.area()))/box.area():0;}
cv::Point2f center(const cv::Rect&b){return {b.x+b.width*.5F,b.y+b.height*.5F};}
bool crossed(const cv::Point2f&p,const cv::Point2f&c,double y,int x1,int x2,const std::string&side){
  bool hit=side=="top"?p.y<=y&&y<c.y:p.y>=y&&y>c.y;
  if(!hit||c.y==p.y)return false;
  double x=p.x+(y-p.y)/(c.y-p.y)*(c.x-p.x);
  return x1<=x&&x<=x2;
}
bool moved(const cv::Rect&a,const cv::Rect&b){auto p=center(a),c=center(b);double th=std::max(5.0,std::min(a.width,a.height)*.05);return std::pow(c.x-p.x,2)+std::pow(c.y-p.y,2)>=th*th;}
cv::Rect clamp_rect(const cv::Rect& box,const cv::Size& size){return box&cv::Rect(0,0,size.width,size.height);}
int plate_input_size(const std::string& path){
  static const int sizes[]={640,608,512,416,384,256};
  for(int size:sizes){
    const std::string token="-"+std::to_string(size)+"-";
    if(path.find(token)!=std::string::npos)return size;
  }
  return 384;
}
cv::Rect expand_plate_box(const cv::Rect& box,const cv::Size& size){
  const int min_side=96;const double pad=0.35;
  int w=std::max({box.width,min_side,int(std::round(box.width*(1+2*pad)))});
  int h=std::max({box.height,min_side,int(std::round(box.height*(1+2*pad)))});
  return clamp_rect({box.x+box.width/2-w/2,box.y+box.height/2-h/2,w,h},size);
}
cv::Mat plate_upload_crop(const cv::Mat& frame,const cv::Rect& box){
  cv::Rect region=expand_plate_box(box,frame.size());
  if(region.width<8||region.height<8)return {};
  return frame(region).clone();
}
cv::Mat detection_rows(const cv::Mat& out){
  if(out.empty())return {};
  int d0=0,d1=0;
  if(out.dims==2){d0=out.rows;d1=out.cols;}
  else if(out.dims==3){d0=out.size[1];d1=out.size[2];}
  else if(out.dims>=4){d0=out.size[out.dims-2];d1=out.size[out.dims-1];}
  else return {};
  cv::Mat flat(d0,d1,CV_32F,const_cast<float*>(out.ptr<float>()));
  cv::Mat rows;
  if(d1>=6&&d1<=8)rows=flat;
  else if(d0>=6&&d0<=8)cv::transpose(flat,rows);
  else if(d0<d1)cv::transpose(flat,rows);
  else rows=flat;
  return rows;
}
std::string ocr_path(std::string path){
  if(path.empty())return "/ocr";
  while(path.size()>1&&path.back()=='/')path.pop_back();
  if(path.size()<4||path.compare(path.size()-4,4,"/ocr")!=0)path+="/ocr";
  return path;
}
std::string b64(const std::vector<uchar>& data){
  static const char*T="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";std::string o;size_t i=0;
  while(i+2<data.size()){uint32_t n=(data[i]<<16)|(data[i+1]<<8)|data[i+2];for(int s:{18,12,6,0})o+=T[(n>>s)&63];i+=3;}
  if(i<data.size()){uint32_t n=data[i]<<16;o+=T[(n>>18)&63];if(i+1<data.size()){n|=data[i+1]<<8;o+=T[(n>>12)&63];o+=T[(n>>6)&63];o+='=';}else{o+=T[(n>>12)&63];o+="==";}}return o;
}
std::string escape_json(const std::string&s){std::string o;for(char c:s){if(c=='\\'||c=='"')o+='\\';o+=c;}return o;}
bool parse_url(const std::string&url,std::string&host,std::string&path,INTERNET_PORT&port,bool&secure){
  URL_COMPONENTSA p{};p.dwStructSize=sizeof(p);char h[512]{},u[2048]{},e[1024]{};
  p.lpszHostName=h;p.dwHostNameLength=sizeof(h);p.lpszUrlPath=u;p.dwUrlPathLength=sizeof(u);p.lpszExtraInfo=e;p.dwExtraInfoLength=sizeof(e);
  if(!InternetCrackUrlA(url.c_str(),0,0,&p))return false;
  host.assign(p.lpszHostName,p.dwHostNameLength);path.assign(p.lpszUrlPath,p.dwUrlPathLength);
  if(p.dwExtraInfoLength)path.append(p.lpszExtraInfo,p.dwExtraInfoLength);
  if(path.empty())path="/";
  port=p.nPort;secure=p.nScheme==INTERNET_SCHEME_HTTPS;return true;
}
bool post_barrier(const Config&c,const std::string&reason){
  if(c.barrier_url.empty())return false;
  std::string host,path;INTERNET_PORT port;bool secure;
  if(!parse_url(c.barrier_url,host,path,port,secure))return false;
  HINTERNET session=InternetOpenA("gate-controller/3.0",INTERNET_OPEN_TYPE_PRECONFIG,nullptr,nullptr,0);
  if(!session)return false;
  HINTERNET connection=InternetConnectA(session,host.c_str(),port,nullptr,nullptr,INTERNET_SERVICE_HTTP,0,0);
  if(!connection){InternetCloseHandle(session);return false;}
  DWORD flags=INTERNET_FLAG_RELOAD|INTERNET_FLAG_NO_CACHE_WRITE|(secure?INTERNET_FLAG_SECURE:0);
  HINTERNET request=HttpOpenRequestA(connection,"POST",path.c_str(),nullptr,nullptr,nullptr,flags,0);
  std::ostringstream body;body<<"{\"command\":\"OPEN\",\"gate_id\":\""<<escape_json(c.gate_id)<<"\",\"reason\":\""<<escape_json(reason)<<"\"}";
  std::string payload=body.str(),headers="Content-Type: application/json\r\n";
  BOOL sent=request&&HttpSendRequestA(request,headers.c_str(),DWORD(headers.size()),payload.data(),DWORD(payload.size()));
  DWORD status=0,size=sizeof(status);if(sent)HttpQueryInfoA(request,HTTP_QUERY_STATUS_CODE|HTTP_QUERY_FLAG_NUMBER,&status,&size,nullptr);
  if(request)InternetCloseHandle(request);
  InternetCloseHandle(connection);InternetCloseHandle(session);
  if(sent&&status>=200&&status<300){
    std::string message=c.gate_name+" barrier opened";
    MessageBoxA(nullptr,message.c_str(),"Parking Barrier",MB_OK|MB_ICONINFORMATION|MB_TOPMOST);
    return true;
  }
  std::cerr<<"["<<c.gate_id<<"] barrier request error status="<<status<<std::endl;
  return false;
}

bool post_ocr(const Config&c,cv::Mat crop){
  if(crop.empty()||c.worker_url.empty()||c.parking_api_key.empty())return false;
  int largest=std::max(crop.cols,crop.rows);
  if(largest>640){double scale=640.0/largest;cv::resize(crop,crop,{},scale,scale,cv::INTER_AREA);}
  else if(largest>0&&largest<160){double scale=160.0/largest;cv::resize(crop,crop,{},scale,scale,cv::INTER_CUBIC);}
  std::vector<uchar> jpg;
  if(!cv::imencode(".jpg",crop,jpg,{cv::IMWRITE_JPEG_QUALITY,88}))return false;
  std::ostringstream body;body<<"{\"parking_id\":\""<<escape_json(c.parking_id)<<"\",\"image_base64\":\""<<b64(jpg)
    <<"\",\"gate_id\":\""<<escape_json(c.gate_id)<<"\",\"gate_name\":\""<<escape_json(c.gate_name)<<"\",\"gate\":\""<<escape_json(c.gate_name)
    <<"\",\"gate_type\":\""<<escape_json(c.gate_type)<<"\",\"event_type\":\""<<escape_json(c.gate_type)<<"\"}";
  std::string host,path;INTERNET_PORT port;bool secure;if(!parse_url(c.worker_url,host,path,port,secure))return false;
  path=ocr_path(path);
  HINTERNET session=InternetOpenA("gate-detector-worker/2.0",INTERNET_OPEN_TYPE_PRECONFIG,nullptr,nullptr,0);if(!session)return false;
  HINTERNET connection=InternetConnectA(session,host.c_str(),port,nullptr,nullptr,INTERNET_SERVICE_HTTP,0,0);if(!connection){InternetCloseHandle(session);return false;}
  DWORD flags=INTERNET_FLAG_RELOAD|INTERNET_FLAG_NO_CACHE_WRITE|(secure?INTERNET_FLAG_SECURE:0);
  HINTERNET request=HttpOpenRequestA(connection,"POST",path.c_str(),nullptr,nullptr,nullptr,flags,0);if(!request){InternetCloseHandle(connection);InternetCloseHandle(session);return false;}
  DWORD timeout=100000;InternetSetOptionA(request,INTERNET_OPTION_CONNECT_TIMEOUT,&timeout,sizeof(timeout));InternetSetOptionA(request,INTERNET_OPTION_RECEIVE_TIMEOUT,&timeout,sizeof(timeout));
  std::string headers="Content-Type: application/json\r\nX-API-Key: "+c.parking_api_key+"\r\n";if(!c.client_token.empty())headers+="X-Client-Token: "+c.client_token+"\r\n";
  std::string payload=body.str();BOOL sent=HttpSendRequestA(request,headers.c_str(),DWORD(headers.size()),const_cast<char*>(payload.data()),DWORD(payload.size()));
  DWORD status=0,size=sizeof(status);if(sent)HttpQueryInfoA(request,HTTP_QUERY_STATUS_CODE|HTTP_QUERY_FLAG_NUMBER,&status,&size,nullptr);
  std::string response;char chunk[4096];while(sent){DWORD read=0;if(!InternetReadFile(request,chunk,sizeof(chunk),&read)||!read)break;response.append(chunk,read);}
  InternetCloseHandle(request);InternetCloseHandle(connection);InternetCloseHandle(session);
  std::cout<<"["<<c.gate_id<<"] OCR crop "<<crop.cols<<"x"<<crop.rows<<" jpg="<<jpg.size()<<" status="<<status<<" body="<<response.substr(0,300)<<std::endl;
  const bool open=response.find("\"action\":\"OPEN\"")!=std::string::npos||response.find("\"action\": \"OPEN\"")!=std::string::npos;
  if(sent&&status==200&&open)return post_barrier(c,"ocr");
  return sent&&status==200;
}

class Detector {
 public:
  explicit Detector(const Config&c):cfg_(c),plate_size_(plate_input_size(c.plate_model_path)){
    vehicle_=cv::dnn::readNetFromONNX(c.vehicle_model_path);
    plate_=cv::dnn::readNetFromONNX(c.plate_model_path);
    std::cerr<<"["<<cfg_.gate_id<<"] plate model "<<cfg_.plate_model_path<<" input "<<plate_size_<<"x"<<plate_size_<<std::endl;
  }
  std::vector<Detection> vehicles(const cv::Mat&frame){
    auto prep=letterbox(frame,640,640);vehicle_.setInput(cv::dnn::blobFromImage(prep.image,1/255.0,{640,640},{},true,false));cv::Mat out,rows;
    try {
      out=vehicle_.forward();
    } catch(const cv::Exception& error) {
      throw std::runtime_error(std::string("vehicle ONNX inference failed: ")+error.what());
    }
    if(out.dims==3&&out.size[1]<out.size[2]){cv::Mat channels(out.size[1],out.size[2],CV_32F,out.ptr<float>());cv::transpose(channels,rows);}else rows=cv::Mat(out.size[1],out.size[2],CV_32F,out.ptr<float>());
    const std::vector<int> allowed={0,2,3,5,7};const std::vector<std::string> names={"person","bicycle","car","motorcycle","airplane","bus","train","truck"};
    std::vector<cv::Rect> boxes;std::vector<float> scores;std::vector<int> classes;
    for(int r=0;r<rows.rows;++r){const float*v=rows.ptr<float>(r);int cls=-1;float score=0;for(int id:allowed)if(4+id<rows.cols&&v[4+id]>score){score=v[4+id];cls=id;}
      if(cls<0||score<cfg_.vehicle_confidence)continue;
      auto b=restore(v[0]-v[2]/2,v[1]-v[3]/2,v[0]+v[2]/2,v[1]+v[3]/2,prep,frame.size());if(b.area()){boxes.push_back(b);scores.push_back(score);classes.push_back(cls);}}
    std::vector<int> kept;cv::dnn::NMSBoxes(boxes,scores,float(cfg_.vehicle_confidence),float(cfg_.vehicle_nms_threshold),kept);std::vector<Detection>d;
    for(int i:kept){
      const std::string label=classes[i]<int(names.size())?names[classes[i]]:"vehicle";
      d.push_back({boxes[i],classes[i],scores[i],label,{}});
    }
    return d;
  }
  std::vector<Detection> plates(const cv::Mat&frame){
    const cv::Size input_size(plate_size_,plate_size_);
    auto prep=letterbox(frame,plate_size_,plate_size_);
    plate_.setInput(cv::dnn::blobFromImage(prep.image,1/255.0,input_size,{},true,false));
    cv::Mat out;
    try {
      out=plate_.forward();
      plate_gather_warning_logged_=false;
    } catch(const cv::Exception& error) {
      const std::string message=error.what();
      if(message.find("gather")!=std::string::npos||message.find("Gather")!=std::string::npos){
        if(!plate_gather_warning_logged_){
          std::cerr<<"["<<cfg_.gate_id<<"] plate model returned no valid NMS indices; treating frame as no plate"<<std::endl;
          plate_gather_warning_logged_=true;
        }
        return {};
      }
      throw std::runtime_error(std::string("plate ONNX inference failed: ")+error.what());
    }
    if(out.empty())return {};
    cv::Mat rows=detection_rows(out);
    std::vector<Detection>d;
    const float min_score=cfg_.plate_confidence>0?float(cfg_.plate_confidence):0.25f;
    auto looks_score=[](float value){return value>=0.f&&value<=1.f;};
    auto looks_box=[&](float x1,float y1,float x2,float y2){
      return x2>x1&&y2>y1&&x1>=-16.f&&y1>=-16.f&&x2<=float(plate_size_+16)&&y2<=float(plate_size_+16);
    };
    for(int r=0;r<rows.rows;++r){
      const float*v=rows.ptr<float>(r);
      float x1=0,y1=0,x2=0,y2=0,score=0,class_id=0;
      const bool seven=rows.cols>=7&&looks_score(v[6])&&looks_box(v[1],v[2],v[3],v[4]);
      if(seven){
        x1=v[1];y1=v[2];x2=v[3];y2=v[4];class_id=v[5];score=v[6];
      }else if(rows.cols>=6&&looks_score(v[4])&&looks_box(v[0],v[1],v[2],v[3])){
        x1=v[0];y1=v[1];x2=v[2];y2=v[3];score=v[4];class_id=v[5];
      }else{
        continue;
      }
      if(score<min_score)continue;
      auto b=restore(x1,y1,x2,y2,prep,frame.size());
      if(b.width<8||b.height<8)continue;
      Detection hit{b,int(class_id),score,"plate",{}};
      cv::Rect raw=clamp_rect({int(std::floor(x1)),int(std::floor(y1)),int(std::ceil(x2-x1)),int(std::ceil(y2-y1))},prep.image.size());
      cv::Rect padded=expand_plate_box(raw,prep.image.size());
      if(padded.width>=8&&padded.height>=8)hit.crop=prep.image(padded).clone();
      d.push_back(std::move(hit));
    }
    std::sort(d.begin(),d.end(),[](const auto&a,const auto&b){return a.confidence>b.confidence;});
    return d;
  }
 private:Config cfg_;cv::dnn::Net vehicle_,plate_;int plate_size_=384;bool plate_gather_warning_logged_=false;
};

class Worker {
 public:
  explicit Worker(Config c):cfg_(std::move(c)),roi_(parse_roi(cfg_.roi_json)),detector_(cfg_){}
  int run(){
    if(roi_.w<=0||roi_.h<=0)return fail("invalid ROI",3);
    if(cfg_.input_mode=="media"&&is_image(cfg_.input_path))return run_image();
    cv::VideoCapture cap;if(is_integer(cfg_.input_path))cap.open(std::stoi(cfg_.input_path),cv::CAP_DSHOW);else{cap.open(cfg_.input_path,cv::CAP_FFMPEG);if(!cap.isOpened())cap.open(cfg_.input_path);}
    if(!cap.isOpened())return fail("unable to open source",2);
    cap.set(cv::CAP_PROP_BUFFERSIZE,1);
    const double source_fps=cap.get(cv::CAP_PROP_FPS);
    const bool pace_media=cfg_.input_mode=="media"&&source_fps>0&&source_fps<=240;
    const auto frame_period=std::chrono::duration<double>(pace_media?1.0/source_fps:0.0);
    auto next_frame=std::chrono::steady_clock::now();
    cv::Mat frame;
    while(cap.read(frame)&&!frame.empty()){
      if(pace_media){
        next_frame+=std::chrono::duration_cast<std::chrono::steady_clock::duration>(frame_period);
        const auto now=std::chrono::steady_clock::now();
        if(next_frame>now)std::this_thread::sleep_until(next_frame);
      }
      process(frame);
    }
    for(auto& task:ocr_futures_)task.get();
    ocr_futures_.clear();
    return 0;
  }
 private:
  int fail(const std::string&m,int code){std::cerr<<"["<<cfg_.gate_id<<"] "<<m<<std::endl;return code;}
  static bool is_integer(const std::string&s){return !s.empty()&&std::all_of(s.begin(),s.end(),[](unsigned char c){return std::isdigit(c);});}
  static bool is_image(std::string s){std::transform(s.begin(),s.end(),s.begin(),[](unsigned char c){return char(std::tolower(c));});for(auto e:{".jpg",".jpeg",".png",".bmp",".webp"})if(s.size()>=strlen(e)&&s.compare(s.size()-strlen(e),strlen(e),e)==0)return true;return false;}
  int run_image(){
    cv::Mat image=cv::imread(cfg_.input_path);if(image.empty())return fail("unable to read image",2);
    auto vehicles=detector_.vehicles(image);auto plates=detector_.plates(image);
    const cv::Rect roi=roi_pixels(roi_,image.size());
    tracks_.clear();
    for(const auto& vehicle:vehicles)tracks_.push_back({vehicle.box,center(vehicle.box),"confirmed",vehicle.label,0,false});
    ++frame_no_;frame_captured_ms_=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    for(const auto& plate:plates){
      if(!roi.contains(center(plate.box)))continue;
      bool belongs=vehicles.empty();
      for(const auto& vehicle:vehicles)if(vehicle.box.contains(center(plate.box))){belongs=true;break;}
      if(belongs){
        if(!vehicles.empty()){active_=vehicles.front().box;has_active_=true;}
        publish_state(image,&plate.box);
        cv::Mat crop=plate.crop.empty()?plate_upload_crop(image,plate.box):plate.crop;
        return post_ocr(cfg_,std::move(crop))?0:5;
      }
    }
    publish_state(image);
    std::cerr<<"["<<cfg_.gate_id<<"] OCR error: no plate found inside temporary ROI"<<std::endl;
    return 4;
  }
  void publish_state(const cv::Mat&frame,const cv::Rect*plate=nullptr){
    const cv::Size size=frame.size();
    if(cfg_.state_path.empty()||size.width<=0||size.height<=0)return;
    auto write_box=[&](std::ostringstream&out,const cv::Rect&box){
      out<<"{\"x\":"<<double(box.x)/size.width<<",\"y\":"<<double(box.y)/size.height
         <<",\"w\":"<<double(box.width)/size.width<<",\"h\":"<<double(box.height)/size.height<<"}";
    };
    std::string published_preview;
    if(!cfg_.preview_path.empty()){
      published_preview=cfg_.preview_path+"."+std::to_string(frame_no_)+".jpg";
      const std::string temporary_preview=published_preview+".tmp";
      cv::Mat preview=frame;
      if(frame.cols>960){
        const double scale=960.0/frame.cols;
        cv::resize(frame,preview,{},scale,scale,cv::INTER_AREA);
      }
      std::vector<uchar> encoded;
      if(cv::imencode(".jpg",preview,encoded,{cv::IMWRITE_JPEG_QUALITY,75})){
        std::ofstream output(temporary_preview,std::ios::binary|std::ios::trunc);
        output.write(reinterpret_cast<const char*>(encoded.data()),static_cast<std::streamsize>(encoded.size()));
        output.close();
        MoveFileExA(temporary_preview.c_str(),published_preview.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH);
      }
    }
    const auto now_ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    std::ostringstream json;json<<"{\"frame\":"<<frame_no_<<",\"captured_ms\":"<<frame_captured_ms_<<",\"published_ms\":"<<now_ms<<",\"processing_ms\":"<<(now_ms-frame_captured_ms_)<<",\"preview_path\":\""<<escape_json(published_preview)<<"\",\"vehicles\":[";bool first=true;
    for(const auto&track:tracks_){
      if(!first)json<<',';
      first=false;std::string status="detected";
      if(has_inactive_&&iou(track.box,inactive_)>=.5)status="inactive";else if(has_active_&&iou(track.box,active_)>=.5)status="active";
      json<<"{\"status\":\""<<status<<"\",\"label\":\""<<escape_json(track.label)<<"\",\"box\":";write_box(json,track.box);json<<'}';
    }
    json<<"],\"plate\":";if(plate)write_box(json,*plate);else json<<"null";json<<'}';
    const std::string temporary=cfg_.state_path+".tmp";
    {std::ofstream output(temporary,std::ios::binary|std::ios::trunc);if(!output)return;output<<json.str();}
    MoveFileExA(temporary.c_str(),cfg_.state_path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH);
  }
  std::vector<Track> update_tracks(const std::vector<Detection>&detections,const cv::Rect&roi){
    double entry_line=cfg_.entry_side=="top"?roi.y+roi.height*cfg_.line_ratio:roi.y+roi.height*(1-cfg_.line_ratio);
    double opposite_line=cfg_.entry_side=="top"?roi.y+roi.height*(1-cfg_.line_ratio):roi.y+roi.height*cfg_.line_ratio;
    std::vector<Track>updated;std::vector<bool>used(tracks_.size());
    for(const auto&d:detections){auto c=center(d.box);int best=-1;double overlap=0;for(int i=0;i<int(tracks_.size());++i)if(!used[i]&&iou(tracks_[i].box,d.box)>overlap){overlap=iou(tracks_[i].box,d.box);best=i;}
      bool matched=best>=0&&overlap>=cfg_.track_iou_threshold;const Track*old=matched?&tracks_[best]:nullptr;if(matched)used[best]=true;
      Track t{d.box,c,old?old->phase:"waiting",d.label,old?old->entry_touch_frame:-1,old?old->opposite_touched:false};auto previous=old?old->center:c;
      bool spans_entry=d.box.x<roi.x+roi.width&&d.box.x+d.box.width>roi.x&&d.box.y<=entry_line&&d.box.y+d.box.height>=entry_line;
      bool spans_opposite=d.box.x<roi.x+roi.width&&d.box.x+d.box.width>roi.x&&d.box.y<=opposite_line&&d.box.y+d.box.height>=opposite_line;
      if(t.entry_touch_frame<0&&spans_opposite)t.opposite_touched=true;
      bool moving_inward=cfg_.entry_side=="top"?c.y>previous.y:c.y<previous.y;
      bool crossed_inward=crossed(previous,c,entry_line,roi.x,roi.x+roi.width,cfg_.entry_side);
      if(t.entry_touch_frame<0&&!t.opposite_touched&&spans_entry&&moving_inward){t.entry_touch_frame=frame_no_;t.phase="armed";}
      if(t.entry_touch_frame>=0&&!t.opposite_touched&&moving_inward&&(crossed_inward||(t.phase=="armed"&&inside_ratio(d.box,roi)>=cfg_.vehicle_roi_percent)))t.phase="confirmed";
      if(t.phase!="confirmed"&&!t.opposite_touched&&inside_ratio(d.box,roi)>=cfg_.vehicle_roi_percent){
        if(t.entry_touch_frame<0)t.entry_touch_frame=frame_no_;
        t.phase="confirmed";
      }
      updated.push_back(t);}
    tracks_=updated;std::vector<Track>confirmed;for(auto&t:tracks_)if(t.phase=="confirmed")confirmed.push_back(t);return confirmed;
  }
  const Track* match(const std::vector<Track>&v,const cv::Rect&r){const Track*best=nullptr;double score=0;for(auto&t:v)if(iou(r,t.box)>score){score=iou(r,t.box);best=&t;}return score>=cfg_.track_iou_threshold?best:nullptr;}
  void update_states(const std::vector<Track>&v){
    if(has_active_){
      auto m=match(v,active_);
      if(!m||inside_ratio(m->box,current_roi_)<cfg_.vehicle_roi_percent){
        missing_+=cfg_.vehicle_detection_interval;
        if(missing_>=cfg_.vehicle_exit_frames){has_active_=false;has_inactive_=false;ocr_sent_for_active_=false;missing_=stationary_=inactive_missing_=0;}
      }else{
        auto previous=active_;active_=m->box;missing_=0;
        if(moved(previous,active_)){stationary_=0;has_inactive_=false;}
        else if((stationary_+=cfg_.vehicle_detection_interval)>=cfg_.vehicle_stationary_frames){inactive_=active_;has_inactive_=true;}
      }
      return;
    }
    const Track*winner=nullptr;
    for(const auto&candidate:v){
      if(candidate.entry_touch_frame<0||inside_ratio(candidate.box,current_roi_)<cfg_.vehicle_roi_percent)continue;
      if(!winner||candidate.entry_touch_frame<winner->entry_touch_frame)winner=&candidate;
    }
    if(winner){active_=winner->box;has_active_=true;has_inactive_=false;ocr_sent_for_active_=false;missing_=stationary_=0;}
  }
  void process_plates(const cv::Mat&frame,const cv::Rect&roi,const std::vector<Track>&vehicles){
    cv::Rect vehicle_crop=active_&cv::Rect(0,0,frame.cols,frame.rows);
    if(vehicle_crop.width<20||vehicle_crop.height<20){publish_state(frame);return;}
    const int pad_x=std::max(12,vehicle_crop.width/5),pad_y=std::max(12,vehicle_crop.height/5);
    cv::Rect search=clamp_rect({vehicle_crop.x-pad_x,vehicle_crop.y-pad_y,vehicle_crop.width+2*pad_x,vehicle_crop.height+2*pad_y},frame.size());
    if(search.width<32||search.height<32)search=roi&cv::Rect(0,0,frame.cols,frame.rows);
    auto plates=detector_.plates(frame(search));
    for(auto&plate:plates){plate.box.x+=search.x;plate.box.y+=search.y;}
    const Detection*picked=nullptr;
    for(auto&p:plates){
      if(!roi.contains(center(p.box)))continue;
      if((p.box&active_).area()<=0)continue;
      picked=&p;
      break;
    }
    if(!picked){
      for(auto&p:plates){
        if(!roi.contains(center(p.box)))continue;
        for(const auto&vehicle:vehicles){
          if((p.box&vehicle.box).area()>0){picked=&p;break;}
        }
        if(picked)break;
      }
    }
    if(!picked){publish_state(frame);return;}
    last_plate_=picked->box;
    last_plate_frame_=frame_no_;
    publish_state(frame,&picked->box);
    if(ocr_sent_for_active_)return;
    cv::Mat crop=picked->crop.empty()?plate_upload_crop(frame,picked->box):picked->crop;
    if(crop.empty())return;
    ocr_sent_for_active_=true;
    std::cerr<<"["<<cfg_.gate_id<<"] sending OCR crop "<<crop.cols<<"x"<<crop.rows
             <<" plate "<<picked->box.width<<"x"<<picked->box.height
             <<" conf="<<picked->confidence<<std::endl;
    ocr_futures_.push_back(std::async(std::launch::async,[config=cfg_,crop=std::move(crop)]() mutable{return post_ocr(config,std::move(crop));}));
  }
  void process(const cv::Mat&frame){
    for(auto it=ocr_futures_.begin();it!=ocr_futures_.end();){
      if(it->wait_for(std::chrono::milliseconds(0))==std::future_status::ready){
        if(!it->get())std::cerr<<"["<<cfg_.gate_id<<"] OCR request failed"<<std::endl;
        it=ocr_futures_.erase(it);
      }else ++it;
    }
    ++frame_no_;
    frame_captured_ms_=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    auto roi=roi_pixels(roi_,frame.size());
    if(roi.width<2||roi.height<2)return;
    current_roi_=roi;
    const bool vehicle_frame=(frame_no_-1)%cfg_.vehicle_detection_interval==0;
    const bool plate_frame=(frame_no_-1)%cfg_.plate_detection_interval==0;
    if(vehicle_frame){vehicles_=update_tracks(detector_.vehicles(frame),roi);update_states(vehicles_);}
    if(has_active_&&!ocr_sent_for_active_&&plate_frame)process_plates(frame,roi,vehicles_);
    else {
      const auto now=std::chrono::steady_clock::now();
      if(vehicle_frame||now-last_preview_publish_>=std::chrono::milliseconds(125)){
        const cv::Rect*plate=last_plate_frame_>=0&&frame_no_-last_plate_frame_<=12?&last_plate_:nullptr;
        publish_state(frame,plate);
        last_preview_publish_=now;
      }
    }
  }
  Config cfg_;
  RectNorm roi_;
  Detector detector_;
  int frame_no_=0;
  int missing_=0;
  int stationary_=0;
  int inactive_missing_=0;
  int plate_enter_=-1;
  int last_plate_frame_=-1;
  long long frame_captured_ms_=0;
  std::vector<Track>tracks_,vehicles_;
  bool has_active_=false,has_inactive_=false,ocr_sent_for_active_=false;
  cv::Rect active_,inactive_,current_roi_,last_plate_;
  std::vector<std::future<bool>>ocr_futures_;
  std::chrono::steady_clock::time_point last_preview_publish_{};
};

Config config_from(int argc,char**argv){
  Args a(argc,argv);Config c;c.config_path=a.get("--config-path");c.gate_id=a.get("--gate-id");c.parking_id=a.get("--parking-id");c.gate_name=a.get("--gate-name");c.gate_type=a.get("--gate-type","entry");c.input_mode=a.get("--input-mode","stream");c.input_path=a.get("--input-path",a.get("--camera-url"));c.entry_side=a.get("--entry-side","bottom");c.roi_json=a.get("--roi-json");c.worker_url=a.get("--worker-url");c.parking_api_key=a.get("--parking-api-key");c.client_token=a.get("--client-token");c.barrier_url=a.get("--barrier-url");c.vehicle_model_path=a.get("--vehicle-model-path");c.plate_model_path=a.get("--plate-model-path");c.state_path=a.get("--state-path");c.preview_path=a.get("--preview-path");
  if(!c.config_path.empty()){
    cv::FileStorage fs(c.config_path,cv::FileStorage::READ|cv::FileStorage::FORMAT_JSON);
    if(!fs.isOpened())throw std::runtime_error("unable to open persistent worker config");
    fs["parking_id"]>>c.parking_id;fs["session_token"]>>c.client_token;fs["api_key"]>>c.parking_api_key;fs["worker_url"]>>c.worker_url;fs["vehicle_model_path"]>>c.vehicle_model_path;fs["plate_model_path"]>>c.plate_model_path;
    cv::FileNode gates=fs["gates"];
    for(auto it=gates.begin();it!=gates.end();++it){std::string id;(*it)["gate_id"]>>id;if(id!=c.gate_id)continue;
      (*it)["gate_name"]>>c.gate_name;(*it)["gate_type"]>>c.gate_type;(*it)["camera_url"]>>c.input_path;(*it)["barrier_url"]>>c.barrier_url;(*it)["entry_side"]>>c.entry_side;
      std::string media_path,media_type;(*it)["media_path"]>>media_path;(*it)["media_input_type"]>>media_type;
      if(!media_path.empty()){c.input_path=media_path;c.input_mode="media";}
      cv::FileNode roi=(*it)["detection_area"];if(!roi.empty()){std::ostringstream r;r<<"{\"x\":"<<double(roi["x"])<<",\"y\":"<<double(roi["y"])<<",\"w\":"<<double(roi["w"])<<",\"h\":"<<double(roi["h"])<<"}";c.roi_json=r.str();}
      cv::FileNode p=(*it)["detection_params"];if(!p.empty()){c.line_ratio=double(p["line_ratio"]);c.track_iou_threshold=double(p["track_iou_threshold"]);c.vehicle_confidence=double(p["vehicle_confidence"]);c.vehicle_roi_percent=double(p["vehicle_roi_percent"]);c.vehicle_detection_interval=int(p["vehicle_detection_interval"]);c.vehicle_exit_frames=int(p["vehicle_exit_frames"]);c.vehicle_stationary_frames=int(p["vehicle_stationary_frames"]);c.plate_detection_interval=int(p["plate_detection_interval"]);c.plate_dwell_frames=int(p["plate_dwell_frames"]);}break;}
  }
  if(!a.get("--input-mode").empty())c.input_mode=a.get("--input-mode");
  if(!a.get("--input-path").empty())c.input_path=a.get("--input-path");
  if(!a.get("--entry-side").empty())c.entry_side=a.get("--entry-side");
  if(!a.get("--roi-json").empty())c.roi_json=a.get("--roi-json");
  if(!a.get("--barrier-url").empty())c.barrier_url=a.get("--barrier-url");
  if(!a.get("--state-path").empty())c.state_path=a.get("--state-path");
  if(!a.get("--preview-path").empty())c.preview_path=a.get("--preview-path");
  auto integer=[&](const char*n,int&t,int min){auto v=a.get(n);if(!v.empty())t=std::max(min,std::stoi(v));};auto decimal=[&](const char*n,double&t,double min,double max){auto v=a.get(n);if(!v.empty())t=std::clamp(std::stod(v),min,max);};
  decimal("--line-ratio",c.line_ratio,.01,.45);integer("--vehicle-detection-interval",c.vehicle_detection_interval,1);integer("--vehicle-exit-frames",c.vehicle_exit_frames,1);integer("--vehicle-stationary-frames",c.vehicle_stationary_frames,1);integer("--plate-detection-interval",c.plate_detection_interval,1);integer("--plate-dwell-frames",c.plate_dwell_frames,2);decimal("--track-iou-threshold",c.track_iou_threshold,.01,.95);decimal("--vehicle-confidence",c.vehicle_confidence,.05,.95);decimal("--vehicle-roi-percent",c.vehicle_roi_percent,.01,1);decimal("--plate-confidence",c.plate_confidence,0,.99);return c;
}
} // namespace

int main(int argc,char**argv){try{Config c=config_from(argc,argv);if(c.gate_id.empty()||c.input_path.empty()||c.roi_json.empty()||c.vehicle_model_path.empty()||c.plate_model_path.empty()){std::cerr<<"Missing required worker arguments"<<std::endl;return 1;}Worker worker(c);return worker.run();}catch(const std::exception&e){std::cerr<<"Worker error: "<<e.what()<<std::endl;return 6;}}
