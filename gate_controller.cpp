#include <algorithm>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <opencv2/core.hpp>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#include <tlhelp32.h>
#include <winhttp.h>

namespace fs = std::filesystem;

#ifndef WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET
#define WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET 114
#endif

namespace {
constexpr UINT WM_STATUS_CHANGED = WM_APP + 1;
constexpr UINT WM_BARRIER_OPENED = WM_APP + 2;
constexpr UINT_PTR STATUS_TIMER = 1;
constexpr int SESSION_INTERVAL_SECONDS = 900;
constexpr int COMMAND_MAX_AGE_SECONDS = 30;
constexpr int STATUS_INTERVAL_SECONDS = 2;

struct Gate {
  std::string id, name, type, camera_url, barrier_url, parking_id;
  std::string state_path, preview_path;
  PROCESS_INFORMATION process{};
  HWND camera_label = nullptr, barrier_label = nullptr, worker_label = nullptr, button = nullptr;
  bool barrier_online = false;
  bool barrier_probed = false;
};

struct App {
  std::string config_path, worker_url, api_key, session_token, parking_id;
  std::vector<std::string> parking_ids;
  std::map<std::string, bool> parking_allowed;
  std::vector<Gate> gates;
  std::atomic<bool> running{true}, subscribed{false}, socket_connected{false};
  std::thread network_thread, status_thread;
  HWND window = nullptr, session_label = nullptr;
  std::mutex nonce_mutex, socket_mutex;
  std::set<std::string> seen_nonces;
};
App app;
HANDLE worker_job = nullptr;
HINTERNET live_socket = nullptr;
size_t next_barrier_probe = 0;

std::string json_escape(const std::string& value) {
  std::string out;
  for (unsigned char c : value) {
    switch (c) {
      case '\\': out += "\\\\"; break;
      case '"': out += "\\\""; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default: if (c >= 0x20) out += static_cast<char>(c);
    }
  }
  return out;
}

std::string json_string(const std::string& text, const std::string& key) {
  const std::string token = "\"" + key + "\"";
  auto pos = text.find(token);
  if (pos == std::string::npos || (pos = text.find(':', pos + token.size())) == std::string::npos) return {};
  pos = text.find('"', pos + 1);
  if (pos == std::string::npos) return {};
  std::string out;
  for (++pos; pos < text.size(); ++pos) {
    char c = text[pos];
    if (c == '"') return out;
    if (c == '\\' && pos + 1 < text.size()) {
      char escaped = text[++pos];
      if (escaped == 'n') out += '\n'; else if (escaped == 'r') out += '\r'; else if (escaped == 't') out += '\t'; else out += escaped;
    } else out += c;
  }
  return {};
}

long long json_integer(const std::string& text, const std::string& key) {
  const std::string token = "\"" + key + "\"";
  auto pos = text.find(token);
  if (pos == std::string::npos || (pos = text.find(':', pos + token.size())) == std::string::npos) return 0;
  return std::strtoll(text.c_str() + pos + 1, nullptr, 10);
}

bool json_flag(const std::string& text, const std::string& key) {
  const std::string token = "\"" + key + "\"";
  auto pos = text.find(token);
  if (pos == std::string::npos || (pos = text.find(':', pos + token.size())) == std::string::npos) return false;
  pos += 1;
  while (pos < text.size() && (text[pos] == ' ' || text[pos] == '\t' || text[pos] == '\n' || text[pos] == '\r')) ++pos;
  return text.compare(pos, 4, "true") == 0;
}

std::wstring widen(const std::string& value) {
  if (value.empty()) return {};
  int length = MultiByteToWideChar(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), nullptr, 0);
  std::wstring output(length, L'\0');
  MultiByteToWideChar(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), output.data(), length);
  return output;
}
std::string narrow(const std::wstring& value) {
  if (value.empty()) return {};
  int length = WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
  std::string output(length, '\0');
  WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), output.data(), length, nullptr, nullptr);
  return output;
}

struct Url { std::wstring host, path; INTERNET_PORT port = 0; bool secure = false; };
bool parse_url(const std::string& raw, Url& result) {
  std::wstring value = widen(raw);
  URL_COMPONENTSW parts{}; parts.dwStructSize = sizeof(parts);
  wchar_t host[512]{}, path[4096]{}, extra[2048]{};
  parts.lpszHostName = host; parts.dwHostNameLength = 512;
  parts.lpszUrlPath = path; parts.dwUrlPathLength = 4096;
  parts.lpszExtraInfo = extra; parts.dwExtraInfoLength = 2048;
  if (!WinHttpCrackUrl(value.c_str(), 0, 0, &parts)) return false;
  result.host.assign(host, parts.dwHostNameLength);
  result.path.assign(path, parts.dwUrlPathLength);
  result.path.append(extra, parts.dwExtraInfoLength);
  if (result.path.empty()) result.path = L"/";
  result.port = parts.nPort;
  result.secure = parts.nScheme == INTERNET_SCHEME_HTTPS;
  return true;
}

struct HttpResult { DWORD status = 0; std::string body; };
HttpResult http_request(const std::string& url, const wchar_t* method, const std::string& body, const std::vector<std::wstring>& headers, DWORD timeout_ms = 10000) {
  HttpResult result;
  Url parsed;
  if (!parse_url(url, parsed)) return result;
  HINTERNET session = WinHttpOpen(L"WyomGateController/1.0", WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, nullptr, nullptr, 0);
  if (!session) return result;
  WinHttpSetTimeouts(session, timeout_ms, timeout_ms, timeout_ms, timeout_ms);
  HINTERNET connection = WinHttpConnect(session, parsed.host.c_str(), parsed.port, 0);
  DWORD flags = parsed.secure ? WINHTTP_FLAG_SECURE : 0;
  HINTERNET request = connection ? WinHttpOpenRequest(connection, method, parsed.path.c_str(), nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, flags) : nullptr;
  for (const auto& header : headers) if (request) WinHttpAddRequestHeaders(request, header.c_str(), static_cast<DWORD>(-1), WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE);
  BOOL sent = request && WinHttpSendRequest(request, WINHTTP_NO_ADDITIONAL_HEADERS, 0, body.empty() ? WINHTTP_NO_REQUEST_DATA : const_cast<char*>(body.data()), static_cast<DWORD>(body.size()), static_cast<DWORD>(body.size()), 0);
  if (sent && WinHttpReceiveResponse(request, nullptr)) {
    DWORD size = sizeof(result.status);
    WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, nullptr, &result.status, &size, nullptr);
    for (;;) {
      DWORD available = 0;
      if (!WinHttpQueryDataAvailable(request, &available) || !available) break;
      std::string chunk(available, '\0'); DWORD read = 0;
      if (!WinHttpReadData(request, chunk.data(), available, &read)) break;
      result.body.append(chunk.data(), read);
    }
  }
  if (request) WinHttpCloseHandle(request);
  if (connection) WinHttpCloseHandle(connection);
  WinHttpCloseHandle(session);
  return result;
}

std::string hex(const unsigned char* bytes, size_t size) {
  static const char* chars = "0123456789abcdef";
  std::string out(size * 2, '0');
  for (size_t i = 0; i < size; ++i) { out[i * 2] = chars[bytes[i] >> 4]; out[i * 2 + 1] = chars[bytes[i] & 15]; }
  return out;
}

std::string hmac_sha256(const std::string& secret, const std::string& message) {
  BCRYPT_ALG_HANDLE algorithm = nullptr; BCRYPT_HASH_HANDLE hash = nullptr;
  DWORD object_size = 0, digest_size = 0, returned = 0;
  if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, BCRYPT_ALG_HANDLE_HMAC_FLAG) < 0) return {};
  BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH, reinterpret_cast<PUCHAR>(&object_size), sizeof(object_size), &returned, 0);
  BCryptGetProperty(algorithm, BCRYPT_HASH_LENGTH, reinterpret_cast<PUCHAR>(&digest_size), sizeof(digest_size), &returned, 0);
  std::vector<unsigned char> object(object_size), digest(digest_size);
  if (BCryptCreateHash(algorithm, &hash, object.data(), object_size, reinterpret_cast<PUCHAR>(const_cast<char*>(secret.data())), static_cast<ULONG>(secret.size()), 0) >= 0) {
    BCryptHashData(hash, reinterpret_cast<PUCHAR>(const_cast<char*>(message.data())), static_cast<ULONG>(message.size()), 0);
    BCryptFinishHash(hash, digest.data(), digest_size, 0);
  }
  if (hash) BCryptDestroyHash(hash);
  BCryptCloseAlgorithmProvider(algorithm, 0);
  return hex(digest.data(), digest.size());
}

bool constant_equal(const std::string& left, const std::string& right) {
  if (left.size() != right.size()) return false;
  unsigned char difference = 0;
  for (size_t i = 0; i < left.size(); ++i) difference |= static_cast<unsigned char>(left[i] ^ right[i]);
  return difference == 0;
}

bool load_config() {
  cv::FileStorage storage(app.config_path, cv::FileStorage::READ | cv::FileStorage::FORMAT_JSON);
  if (!storage.isOpened()) return false;
  storage["worker_url"] >> app.worker_url;
  storage["api_key"] >> app.api_key;
  storage["session_token"] >> app.session_token;
  storage["parking_id"] >> app.parking_id;
  app.parking_ids.clear();
  app.parking_allowed.clear();
  for (auto node : storage["parking_ids"]) {
    std::string parking_id;
    node >> parking_id;
    if (!parking_id.empty() && std::find(app.parking_ids.begin(), app.parking_ids.end(), parking_id) == app.parking_ids.end()) {
      app.parking_ids.push_back(parking_id);
    }
  }
  if (app.parking_ids.empty() && !app.parking_id.empty()) app.parking_ids.push_back(app.parking_id);
  if (app.parking_id.empty() && !app.parking_ids.empty()) app.parking_id = app.parking_ids.front();
  if (app.worker_url.size() >= 4 && app.worker_url.compare(app.worker_url.size() - 4, 4, "/ocr") == 0) app.worker_url.resize(app.worker_url.size() - 4);
  app.gates.clear();
  const fs::path state_dir = fs::path(app.config_path).parent_path() / "cpp" / "state" / "controller";
  fs::create_directories(state_dir);
  for (auto node : storage["gates"]) {
    Gate gate;
    node["gate_id"] >> gate.id; node["gate_name"] >> gate.name; node["gate_type"] >> gate.type;
    node["camera_url"] >> gate.camera_url; node["barrier_url"] >> gate.barrier_url;
    node["parking_id"] >> gate.parking_id;
    if (gate.parking_id.empty()) gate.parking_id = app.parking_id;
    gate.state_path = (state_dir / (gate.id + ".json")).string();
    gate.preview_path = (state_dir / (gate.id + ".jpg")).string();
    if (!gate.id.empty()) app.gates.push_back(std::move(gate));
  }
  for (const auto& gate : app.gates) {
    if (!gate.parking_id.empty() && std::find(app.parking_ids.begin(), app.parking_ids.end(), gate.parking_id) == app.parking_ids.end()) {
      app.parking_ids.push_back(gate.parking_id);
    }
  }
  if (app.parking_id.empty() && !app.parking_ids.empty()) app.parking_id = app.parking_ids.front();
  return !app.worker_url.empty() && !app.api_key.empty() && !app.gates.empty();
}

fs::path executable_directory() {
  std::wstring buffer(32768, L'\0');
  DWORD size = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
  buffer.resize(size);
  return fs::path(buffer).parent_path();
}

std::string gate_parking_id(const Gate& gate) {
  return !gate.parking_id.empty() ? gate.parking_id : app.parking_id;
}

bool parking_allowed_for(const Gate& gate) {
  auto found = app.parking_allowed.find(gate_parking_id(gate));
  if (found != app.parking_allowed.end()) return found->second;
  return app.subscribed.load();
}

bool owns_parking(const std::string& parking_id) {
  if (parking_id.empty()) return false;
  if (parking_id == app.parking_id) return true;
  return std::find(app.parking_ids.begin(), app.parking_ids.end(), parking_id) != app.parking_ids.end();
}

void stop_gate(Gate& gate) {
  if (gate.process.hProcess) {
    if (WaitForSingleObject(gate.process.hProcess, 0) == WAIT_TIMEOUT) TerminateProcess(gate.process.hProcess, 0);
    CloseHandle(gate.process.hThread); CloseHandle(gate.process.hProcess); gate.process = {};
  }
}

bool worker_running(const Gate& gate) { return gate.process.hProcess && WaitForSingleObject(gate.process.hProcess, 0) == WAIT_TIMEOUT; }

void start_gate(Gate& gate) {
  if (!parking_allowed_for(gate) || worker_running(gate) || gate.camera_url.empty()) return;
  fs::path core = executable_directory() / "gate_detector_core.exe";
  std::wostringstream command;
  command << L'"' << core.wstring() << L"\" --config-path \"" << widen(app.config_path) << L"\" --gate-id \"" << widen(gate.id)
          << L"\" --state-path \"" << widen(gate.state_path) << L"\" --preview-path \"" << widen(gate.preview_path) << L'"';
  std::wstring mutable_command = command.str();
  STARTUPINFOW startup{}; startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESHOWWINDOW; startup.wShowWindow = SW_HIDE;
  if (!CreateProcessW(nullptr, mutable_command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, executable_directory().wstring().c_str(), &startup, &gate.process)) gate.process = {};
  else if (worker_job && !AssignProcessToJobObject(worker_job, gate.process.hProcess)) {
    TerminateProcess(gate.process.hProcess, 1);
    CloseHandle(gate.process.hThread); CloseHandle(gate.process.hProcess); gate.process = {};
  }
}

bool state_fresh(const Gate& gate) {
  std::error_code error;
  auto modified = fs::last_write_time(gate.state_path, error);
  if (error) return false;
  return fs::file_time_type::clock::now() - modified < std::chrono::seconds(10);
}

void stop_all_workers() { for (auto& gate : app.gates) stop_gate(gate); }
void sync_workers() { for (auto& gate : app.gates) { if (parking_allowed_for(gate)) start_gate(gate); else stop_gate(gate); } }

bool open_barrier(Gate& gate, const std::string& reason) {
  if (!parking_allowed_for(gate) || gate.barrier_url.empty()) return false;
  std::ostringstream body; body << "{\"command\":\"OPEN\",\"gate_id\":\"" << json_escape(gate.id) << "\",\"reason\":\"" << json_escape(reason) << "\"}";
  HttpResult response = http_request(gate.barrier_url, L"POST", body.str(), {L"Content-Type: application/json"}, 5000);
  gate.barrier_probed = true;
  gate.barrier_online = response.status >= 200 && response.status < 300;
  if (gate.barrier_online) {
    auto* name = new std::string(gate.name);
    PostMessageW(app.window, WM_BARRIER_OPENED, 0, reinterpret_cast<LPARAM>(name));
    PostMessageW(app.window, WM_STATUS_CHANGED, 0, 0);
    return true;
  }
  std::cerr << "[" << gate.id << "] barrier request error HTTP " << response.status << std::endl;
  return false;
}

Gate* find_gate(const std::string& gate_id) {
  auto found = std::find_if(app.gates.begin(), app.gates.end(), [&](const Gate& gate) { return gate.id == gate_id; });
  return found == app.gates.end() ? nullptr : &*found;
}

bool accept_nonce(const std::string& nonce) {
  if (nonce.size() < 24 || nonce.size() > 128) return false;
  std::lock_guard<std::mutex> lock(app.nonce_mutex);
  if (!app.seen_nonces.insert(nonce).second) return false;
  if (app.seen_nonces.size() > 2048) app.seen_nonces.clear();
  return true;
}

bool verify_and_execute(const std::string& message) {
  const std::string command = json_string(message, "command"), gate_id = json_string(message, "gate_id");
  const std::string nonce = json_string(message, "nonce"), signature = json_string(message, "signature");
  const long long issued_at = json_integer(message, "issued_at");
  const long long now = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
  if (command != "OPEN" || std::llabs(now - issued_at) > COMMAND_MAX_AGE_SECONDS * 1000LL || !accept_nonce(nonce)) return false;
  const std::string canonical = "OPEN\n" + gate_id + "\n" + std::to_string(issued_at) + "\n" + nonce;
  if (!constant_equal(hmac_sha256(app.api_key, canonical), signature)) return false;
  Gate* gate = find_gate(gate_id);
  return gate && open_barrier(*gate, "cloud");
}

struct SocketConn { HINTERNET session = nullptr, connection = nullptr, socket = nullptr; };

void write_gate_status(std::ostringstream& out, const Gate& gate) {
  const char* camera = state_fresh(gate) ? "online" : "offline";
  const char* barrier = gate.barrier_url.empty() ? "unconfigured" : (!gate.barrier_probed ? "ready" : (gate.barrier_online ? "online" : "offline"));
  const char* worker = worker_running(gate) ? "running" : (parking_allowed_for(gate) ? "stopped" : "paused");
  out << "{\"gate_id\":\"" << json_escape(gate.id)
      << "\",\"parking_id\":\"" << json_escape(gate_parking_id(gate))
      << "\",\"camera\":\"" << camera
      << "\",\"barrier\":\"" << barrier
      << "\",\"worker\":\"" << worker << "\"}";
}

std::string build_status_json() {
  std::map<std::string, std::vector<const Gate*>> by_parking;
  for (const auto& gate : app.gates) by_parking[gate_parking_id(gate)].push_back(&gate);
  std::ostringstream out;
  out << "{\"type\":\"status\",\"parking_id\":\"" << json_escape(app.parking_id)
      << "\",\"parking_ids\":[";
  for (size_t i = 0; i < app.parking_ids.size(); ++i) {
    if (i) out << ',';
    out << '"' << json_escape(app.parking_ids[i]) << '"';
  }
  out << "],\"websocket\":\"" << (app.socket_connected ? "online" : "offline")
      << "\",\"subscribed\":" << (app.subscribed ? "true" : "false") << ",\"gates\":[";
  for (size_t i = 0; i < app.gates.size(); ++i) {
    if (i) out << ',';
    write_gate_status(out, app.gates[i]);
  }
  out << "],\"parkings\":{";
  bool first_parking = true;
  for (const auto& entry : by_parking) {
    if (!first_parking) out << ',';
    first_parking = false;
    auto allowed = app.parking_allowed.find(entry.first);
    const bool subscribed = allowed != app.parking_allowed.end() ? allowed->second : app.subscribed.load();
    out << '"' << json_escape(entry.first) << "\":{\"type\":\"status\",\"parking_id\":\"" << json_escape(entry.first)
        << "\",\"websocket\":\"" << (app.socket_connected ? "online" : "offline")
        << "\",\"subscribed\":" << (subscribed ? "true" : "false") << ",\"gates\":[";
    for (size_t i = 0; i < entry.second.size(); ++i) {
      if (i) out << ',';
      write_gate_status(out, *entry.second[i]);
    }
    out << "]}";
  }
  out << "}}";
  return out.str();
}

bool send_status(HINTERNET socket) {
  if (!socket) return false;
  const std::string payload = build_status_json();
  return WinHttpWebSocketSend(socket, WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE, const_cast<char*>(payload.data()), static_cast<DWORD>(payload.size())) == NO_ERROR;
}

void set_live_socket(HINTERNET socket) {
  std::lock_guard<std::mutex> lock(app.socket_mutex);
  live_socket = socket;
}

bool send_live_status() {
  std::lock_guard<std::mutex> lock(app.socket_mutex);
  return live_socket && send_status(live_socket);
}

void close_live_socket() {
  std::lock_guard<std::mutex> lock(app.socket_mutex);
  if (!live_socket) return;
  WinHttpWebSocketClose(live_socket, WINHTTP_WEB_SOCKET_SUCCESS_CLOSE_STATUS, nullptr, 0);
}

bool tcp_reachable(const std::string& url, DWORD timeout_ms = 1500) {
  Url parsed;
  if (!parse_url(url, parsed)) return false;
  ADDRINFOW hints{};
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  hints.ai_protocol = IPPROTO_TCP;
  ADDRINFOW* info = nullptr;
  const std::wstring port = std::to_wstring(parsed.port);
  if (GetAddrInfoW(parsed.host.c_str(), port.c_str(), &hints, &info) != 0) return false;
  SOCKET sock = socket(info->ai_family, info->ai_socktype, info->ai_protocol);
  if (sock == INVALID_SOCKET) {
    FreeAddrInfoW(info);
    return false;
  }
  u_long nonblocking = 1;
  ioctlsocket(sock, FIONBIO, &nonblocking);
  connect(sock, info->ai_addr, static_cast<int>(info->ai_addrlen));
  fd_set write_set;
  FD_ZERO(&write_set);
  FD_SET(sock, &write_set);
  TIMEVAL wait{};
  wait.tv_sec = static_cast<long>(timeout_ms / 1000);
  wait.tv_usec = static_cast<long>((timeout_ms % 1000) * 1000);
  bool ok = select(0, nullptr, &write_set, nullptr, &wait) > 0;
  closesocket(sock);
  FreeAddrInfoW(info);
  return ok;
}

void probe_next_barrier() {
  if (app.gates.empty()) return;
  Gate& gate = app.gates[next_barrier_probe % app.gates.size()];
  next_barrier_probe += 1;
  if (gate.barrier_url.empty()) {
    gate.barrier_probed = true;
    gate.barrier_online = false;
    return;
  }
  gate.barrier_probed = true;
  gate.barrier_online = tcp_reachable(gate.barrier_url, 1500);
}

SocketConn connect_websocket(const std::string& ticket) {
  SocketConn result;
  Url parsed;
  if (!parse_url(app.worker_url + "/barrier/ws?ticket=" + ticket, parsed)) return result;
  HINTERNET session = WinHttpOpen(L"WyomGateController/1.0", WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, nullptr, nullptr, 0);
  if (session) WinHttpSetTimeouts(session, 5000, 5000, 5000, STATUS_INTERVAL_SECONDS * 1000);
  HINTERNET connection = session ? WinHttpConnect(session, parsed.host.c_str(), parsed.port, 0) : nullptr;
  HINTERNET request = connection ? WinHttpOpenRequest(connection, L"GET", parsed.path.c_str(), nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, parsed.secure ? WINHTTP_FLAG_SECURE : 0) : nullptr;
  if (!request || !WinHttpSetOption(request, WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET, nullptr, 0) || !WinHttpSendRequest(request, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) || !WinHttpReceiveResponse(request, nullptr)) {
    if (request) WinHttpCloseHandle(request);
    if (connection) WinHttpCloseHandle(connection);
    if (session) WinHttpCloseHandle(session);
    return result;
  }
  DWORD status = 0, size = sizeof(status); WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, nullptr, &status, &size, nullptr);
  result.socket = status == 101 ? WinHttpWebSocketCompleteUpgrade(request, 0) : nullptr;
  WinHttpCloseHandle(request);
  if (!result.socket) {
    if (connection) WinHttpCloseHandle(connection);
    if (session) WinHttpCloseHandle(session);
    return {};
  }
  result.session = session;
  result.connection = connection;
  return result;
}

HttpResult check_subscription() {
  std::vector<std::wstring> headers = {L"X-API-Key: " + widen(app.api_key)};
  if (!app.parking_id.empty()) headers.push_back(L"X-Parking-Id: " + widen(app.parking_id));
  return http_request(app.worker_url + "/barrier/device-session", L"GET", "", headers);
}

bool apply_subscription(DWORD status) {
  bool active = status == 200;
  bool was_active = app.subscribed.exchange(active);
  if (active != was_active) { sync_workers(); PostMessageW(app.window, WM_STATUS_CHANGED, 0, 0); }
  return active;
}

bool verify_and_execute(const std::string& message);

void apply_access_message(const std::string& message) {
  const std::string parking_id = json_string(message, "parking_id");
  const bool allowed = json_flag(message, "allowed");
  if (parking_id.empty()) {
    for (const auto& id : app.parking_ids) app.parking_allowed[id] = allowed;
    apply_subscription(allowed ? 200 : 403);
    return;
  }
  if (!owns_parking(parking_id)) return;
  app.parking_allowed[parking_id] = allowed;
  bool any = false;
  for (const auto& id : app.parking_ids) {
    auto found = app.parking_allowed.find(id);
    any = any || (found != app.parking_allowed.end() && found->second);
  }
  bool was_active = app.subscribed.exchange(any);
  sync_workers();
  if (any != was_active) PostMessageW(app.window, WM_STATUS_CHANGED, 0, 0);
}

void handle_socket_message(const std::string& message) {
  if (json_string(message, "type") == "access") {
    apply_access_message(message);
    return;
  }
  verify_and_execute(message);
}

void network_loop() {
  bool support_popup_shown = false;
  while (app.running) {
    HttpResult ticket_response = http_request(app.worker_url + "/barrier/socket-ticket", L"POST", "", {L"X-API-Key: " + widen(app.api_key)});
    if (ticket_response.status != 200) {
      if (ticket_response.status == 401 || ticket_response.status == 403) {
        apply_subscription(ticket_response.status);
        if (!support_popup_shown) {
          support_popup_shown = true;
          MessageBoxW(app.window, L"Access is inactive. Contact Support.", L"Parking Subscription", MB_OK | MB_ICONWARNING | MB_TOPMOST);
        }
      }
      int wait_seconds = (ticket_response.status == 401 || ticket_response.status == 403) ? SESSION_INTERVAL_SECONDS : 3;
      for (int i = 0; i < wait_seconds && app.running; ++i) Sleep(1000);
      continue;
    }
    support_popup_shown = false;
    std::string ticket = json_string(ticket_response.body, "ticket");
    SocketConn conn = ticket.empty() ? SocketConn{} : connect_websocket(ticket);
    HINTERNET ws = conn.socket;
    app.socket_connected = ws != nullptr;
    set_live_socket(ws);
    send_live_status();
    PostMessageW(app.window, WM_STATUS_CHANGED, 0, 0);
    while (ws && app.running) {
      char buffer[8192]; DWORD read = 0; WINHTTP_WEB_SOCKET_BUFFER_TYPE type;
      DWORD error = WinHttpWebSocketReceive(ws, buffer, sizeof(buffer) - 1, &read, &type);
      if (error == ERROR_WINHTTP_TIMEOUT) continue;
      if (error != NO_ERROR || type == WINHTTP_WEB_SOCKET_CLOSE_BUFFER_TYPE) break;
      if (type == WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE) {
        handle_socket_message(std::string(buffer, read));
        send_live_status();
      }
    }
    set_live_socket(nullptr);
    if (ws) { WinHttpWebSocketClose(ws, WINHTTP_WEB_SOCKET_SUCCESS_CLOSE_STATUS, nullptr, 0); WinHttpCloseHandle(ws); }
    if (conn.connection) WinHttpCloseHandle(conn.connection);
    if (conn.session) WinHttpCloseHandle(conn.session);
    app.socket_connected = false; PostMessageW(app.window, WM_STATUS_CHANGED, 0, 0);
    if (app.running && app.subscribed) Sleep(250);
  }
}

void status_loop() {
  while (app.running) {
    probe_next_barrier();
    if (app.socket_connected) {
      if (!send_live_status()) close_live_socket();
      else PostMessageW(app.window, WM_STATUS_CHANGED, 0, 0);
    }
    for (int i = 0; i < STATUS_INTERVAL_SECONDS * 10 && app.running; ++i) Sleep(100);
  }
}

void request_manual_open(Gate& gate) {
  std::thread([&gate] {
    if (!parking_allowed_for(gate) || app.session_token.empty()) return;
    std::ostringstream body; body << "{\"gate_id\":\"" << json_escape(gate.id) << "\"}";
    HttpResult response = http_request(app.worker_url + "/barrier/open", L"POST", body.str(), {L"Content-Type: application/json", L"X-Client-Token: " + widen(app.session_token)});
    if (response.status < 200 || response.status >= 300) std::cerr << "[" << gate.id << "] cloud open request error HTTP " << response.status << std::endl;
  }).detach();
}

void set_status(HWND label, const wchar_t* text, bool healthy) {
  SetWindowTextW(label, text);
  InvalidateRect(label, nullptr, TRUE);
  SetWindowLongPtrW(label, GWLP_USERDATA, healthy ? 1 : 0);
}

LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
  if (message == WM_CTLCOLORSTATIC) {
    HDC dc = reinterpret_cast<HDC>(wparam); HWND control = reinterpret_cast<HWND>(lparam);
    SetBkMode(dc, TRANSPARENT); SetTextColor(dc, GetWindowLongPtrW(control, GWLP_USERDATA) ? RGB(32, 190, 90) : RGB(220, 65, 65));
    return reinterpret_cast<LRESULT>(GetStockObject(WHITE_BRUSH));
  }
  if (message == WM_COMMAND) {
    int id = LOWORD(wparam);
    if (id >= 1000 && id < 1000 + static_cast<int>(app.gates.size())) request_manual_open(app.gates[id - 1000]);
    return 0;
  }
  if (message == WM_TIMER || message == WM_STATUS_CHANGED) {
    set_status(app.session_label, app.subscribed ? (app.socket_connected ? L"ACTIVE / CONNECTED" : L"ACTIVE / CONNECTING") : L"INACTIVE - CONTACT SUPPORT", app.subscribed);
    for (auto& gate : app.gates) {
      if (gate.process.hProcess && !worker_running(gate)) { CloseHandle(gate.process.hThread); CloseHandle(gate.process.hProcess); gate.process = {}; }
      if (parking_allowed_for(gate)) start_gate(gate);
      set_status(gate.camera_label, state_fresh(gate) ? L"Camera: ONLINE" : L"Camera: OFFLINE", state_fresh(gate));
      set_status(gate.barrier_label, gate.barrier_url.empty() ? L"Barrier: NOT CONFIGURED" : (!gate.barrier_probed ? L"Barrier: CHECKING" : (gate.barrier_online ? L"Barrier: ONLINE" : L"Barrier: OFFLINE")), !gate.barrier_url.empty() && gate.barrier_online);
      set_status(gate.worker_label, worker_running(gate) ? L"Worker: RUNNING" : (parking_allowed_for(gate) ? L"Worker: STOPPED" : L"Worker: PAUSED"), worker_running(gate));
      EnableWindow(gate.button, parking_allowed_for(gate) && !gate.barrier_url.empty());
    }
    return 0;
  }
  if (message == WM_BARRIER_OPENED) {
    std::unique_ptr<std::string> name(reinterpret_cast<std::string*>(lparam));
    std::wstring text = widen(*name + " barrier opened");
    MessageBoxW(window, text.c_str(), L"Parking Barrier", MB_OK | MB_ICONINFORMATION | MB_TOPMOST);
    return 0;
  }
  if (message == WM_CLOSE) { ShowWindow(window, SW_MINIMIZE); return 0; }
  if (message == WM_DESTROY) { app.running = false; stop_all_workers(); PostQuitMessage(0); return 0; }
  return DefWindowProcW(window, message, wparam, lparam);
}

bool create_ui(HINSTANCE instance) {
  WNDCLASSW klass{}; klass.lpfnWndProc = window_proc; klass.hInstance = instance; klass.lpszClassName = L"WyomGateController"; klass.hCursor = LoadCursor(nullptr, IDC_ARROW); klass.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
  if (!RegisterClassW(&klass) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) return false;
  int height = 125 + static_cast<int>(app.gates.size()) * 115;
  app.window = CreateWindowExW(WS_EX_APPWINDOW, klass.lpszClassName, L"Wyom Parking Gate Controller", WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX, CW_USEDEFAULT, CW_USEDEFAULT, 720, std::min(height, 850), nullptr, nullptr, instance, nullptr);
  if (!app.window) return false;
  CreateWindowW(L"STATIC", L"Subscription / Session", WS_CHILD | WS_VISIBLE, 20, 16, 180, 24, app.window, nullptr, instance, nullptr);
  app.session_label = CreateWindowW(L"STATIC", L"CHECKING", WS_CHILD | WS_VISIBLE, 210, 16, 300, 24, app.window, nullptr, instance, nullptr);
  int y = 55;
  for (size_t i = 0; i < app.gates.size(); ++i, y += 110) {
    Gate& gate = app.gates[i];
    std::wstring title = widen(gate.name + " (" + gate.type + ")");
    CreateWindowW(L"BUTTON", title.c_str(), WS_CHILD | WS_VISIBLE | BS_GROUPBOX, 15, y, 670, 100, app.window, nullptr, instance, nullptr);
    gate.camera_label = CreateWindowW(L"STATIC", L"Camera: CHECKING", WS_CHILD | WS_VISIBLE, 30, y + 28, 180, 22, app.window, nullptr, instance, nullptr);
    gate.barrier_label = CreateWindowW(L"STATIC", L"Barrier: CHECKING", WS_CHILD | WS_VISIBLE, 225, y + 28, 190, 22, app.window, nullptr, instance, nullptr);
    gate.worker_label = CreateWindowW(L"STATIC", L"Worker: CHECKING", WS_CHILD | WS_VISIBLE, 430, y + 28, 140, 22, app.window, nullptr, instance, nullptr);
    gate.button = CreateWindowW(L"BUTTON", L"Open Barrier", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 510, y + 58, 150, 28, app.window, reinterpret_cast<HMENU>(1000 + i), instance, nullptr);
  }
  SetTimer(app.window, STATUS_TIMER, 2000, nullptr);
  ShowWindow(app.window, SW_SHOW); UpdateWindow(app.window); return true;
}

void register_startup() {
  HKEY key = nullptr;
  if (RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\Microsoft\\Windows\\CurrentVersion\\Run", 0, nullptr, 0, KEY_SET_VALUE, nullptr, &key, nullptr) != ERROR_SUCCESS) return;
  const std::wstring command = L"\"" + (executable_directory() / "gate_detector_worker.exe").wstring() + L"\" --config-path \"" + widen(app.config_path) + L"\"";
  RegSetValueExW(key, L"WyomParkingGateController", 0, REG_SZ, reinterpret_cast<const BYTE*>(command.c_str()), static_cast<DWORD>((command.size() + 1) * sizeof(wchar_t)));
  RegCloseKey(key);
}

void terminate_stale_cores() {
  HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snapshot == INVALID_HANDLE_VALUE) return;
  PROCESSENTRY32W entry{}; entry.dwSize = sizeof(entry);
  if (Process32FirstW(snapshot, &entry)) {
    do {
      if (_wcsicmp(entry.szExeFile, L"gate_detector_core.exe") != 0) continue;
      HANDLE process = OpenProcess(PROCESS_TERMINATE, FALSE, entry.th32ProcessID);
      if (process) { TerminateProcess(process, 0); CloseHandle(process); }
    } while (Process32NextW(snapshot, &entry));
  }
  CloseHandle(snapshot);
}

bool create_worker_job() {
  worker_job = CreateJobObjectW(nullptr, nullptr);
  if (!worker_job) return false;
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
  limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  return SetInformationJobObject(worker_job, JobObjectExtendedLimitInformation, &limits, sizeof(limits));
}
} // namespace

int run_application(HINSTANCE instance, const std::wstring& command_line_value) {
  std::wstring args = command_line_value;
  std::string value = narrow(args);
  const std::string marker = "--config-path";
  auto pos = value.find(marker);
  if (pos != std::string::npos) {
    pos += marker.size(); while (pos < value.size() && std::isspace(static_cast<unsigned char>(value[pos]))) ++pos;
    if (pos < value.size() && value[pos] == '"') { auto end = value.find('"', ++pos); app.config_path = value.substr(pos, end - pos); }
    else { auto end = value.find(' ', pos); app.config_path = value.substr(pos, end - pos); }
  }
  if (app.config_path.empty()) app.config_path = (executable_directory().parent_path() / "worker_config.json").string();
  HANDLE singleton = CreateMutexW(nullptr, TRUE, L"Global\\WyomParkingGateController");
  if (!singleton || GetLastError() == ERROR_ALREADY_EXISTS) return 0;
  if (!load_config()) { MessageBoxW(nullptr, L"worker_config.json is missing or incomplete. Configure the gates and API key first.", L"Parking Controller", MB_OK | MB_ICONERROR); return 2; }
  WSADATA winsock{};
  WSAStartup(MAKEWORD(2, 2), &winsock);
  terminate_stale_cores();
  if (!create_worker_job()) return 3;
  register_startup();
  if (!create_ui(instance)) return 4;
  app.status_thread = std::thread(status_loop);
  app.network_thread = std::thread(network_loop);
  MSG message; while (GetMessageW(&message, nullptr, 0, 0) > 0) { TranslateMessage(&message); DispatchMessageW(&message); }
  app.running = false;
  close_live_socket();
  if (app.status_thread.joinable()) app.status_thread.join();
  if (app.network_thread.joinable()) app.network_thread.join();
  WSACleanup();
  if (worker_job) CloseHandle(worker_job); ReleaseMutex(singleton); CloseHandle(singleton); return 0;
}

int WINAPI WinMain(HINSTANCE instance, HINSTANCE, LPSTR, int) {
  return run_application(instance, GetCommandLineW());
}
