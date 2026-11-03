// ESP32 Wi-Fi access point + WiFiServer for a sketch that serves its own control page.
// In the simulator the HTTP link is carried by the BLE-bridge of the viewer's app panel: the machine's operator page writes the HTTP request
// text to characteristic "http-rx" (mail type 2), the sketch reads it from WiFiClient and everything it prints goes back as notifications
// on "http-tx"; client.stop() sends "\x04" (end of response). The SSID is reported as the device name (hook ble.name).
// One request at a time, GET only (a request ends at its blank line; a long one arrives in several mails), the sketch must call
// client.stop(); no STA mode, no TCP timing. Uses the same bridge as BLEDevice.h, so a sketch cannot include both.
#pragma once
#ifdef VC_BLE_BRIDGE
#error "WiFi.h and BLEDevice.h both use the simulator's BLE bridge; a sketch with both is not supported"
#endif
#define VC_BLE_BRIDGE
#include "vc_ext.h"
#include <deque>
#define WL_CONNECTED 3
#define WIFI_AP 2
class IPAddress {
  uint8_t a[4];
 public:
  IPAddress(uint8_t x = 0, uint8_t y = 0, uint8_t z = 0, uint8_t w = 0) { a[0] = x; a[1] = y; a[2] = z; a[3] = w; }
  operator String() const { char b[20]; snprintf(b, sizeof b, "%d.%d.%d.%d", a[0], a[1], a[2], a[3]); return String(b); }
  String toString() const { return String(*this); }
};
struct VcHttp {
  std::deque<std::string> pending;
  std::string cur, part;
  size_t pos = 0;
  int id = 0;
  bool open = false;
};
inline VcHttp vc_http;
inline void vc_http_rx(const VcMsg& m) {
  vc_http.part.append((const char*)m.data, m.len);
  for (size_t e; (e = vc_http.part.find("\r\n\r\n")) != std::string::npos; vc_http.part.erase(0, e + 4)) vc_http.pending.push_back(vc_http.part.substr(0, e + 4));
}
inline int vc_http_reg = (vc_register_mail(2, vc_http_rx), 0);
inline void vc_http_send(const char* p, size_t n) { if (n) vc_js_ble_notify("http-tx", (const uint8_t*)p, (int)n, vc_now()); vc_advance(n * 0.1 + 20); }

class WiFiClient {
  int id = 0;
  bool mine() const { return id && vc_http.open && vc_http.id == id; }
 public:
  WiFiClient() {}
  explicit WiFiClient(int i) : id(i) {}
  operator bool() const { return mine(); }
  bool connected() { vc_advance(VC_COST_MISC); return mine(); }
  int available() { vc_advance(VC_COST_MISC); return mine() ? (int)(vc_http.cur.size() - vc_http.pos) : 0; }
  int read() { vc_advance(VC_COST_MISC); return mine() && vc_http.pos < vc_http.cur.size() ? (uint8_t)vc_http.cur[vc_http.pos++] : -1; }
  size_t print(const String& s) { if (mine()) vc_http_send(s.c_str(), s.length()); return s.length(); }
  size_t print(const char* s) { return print(String(s)); }
  size_t println() { return print("\r\n"); }
  size_t println(const String& s) { return print(s + "\r\n"); }
  size_t println(const char* s) { return println(String(s)); }
  size_t write(uint8_t c) { char b[2] = {(char)c, 0}; return print(b); }
  void stop() { if (mine()) { const char e = 4; vc_http_send(&e, 1); vc_http.open = false; } }
};
class WiFiServer {
 public:
  explicit WiFiServer(int port = 80) { (void)port; }
  void begin() {}
  WiFiClient available() {
    vc_advance(VC_COST_MISC);
    if (vc_http.open || vc_http.pending.empty()) return WiFiClient();
    vc_http.cur = vc_http.pending.front(); vc_http.pending.pop_front(); vc_http.pos = 0; vc_http.open = true;
    return WiFiClient(++vc_http.id);
  }
  WiFiClient accept() { return available(); }
};
class WiFiClass {
 public:
  bool softAP(const char* ssid, const char* pass = nullptr, int ch = 1, int hidden = 0, int maxc = 4) {
    (void)pass; (void)ch; (void)hidden; (void)maxc;
    std::string n = std::string("ble.name:") + ssid;
    vc_js_hook(n.c_str(), 0, 0, vc_now());
    vc_js_hook("ble.service:http", 0, 0, vc_now());
    vc_js_hook("ble.char:http-rx", 2, 0, vc_now());
    vc_js_hook("ble.char:http-tx", 4, 0, vc_now());
    vc_advance(1000);
    return true;
  }
  bool softAPConfig(IPAddress, IPAddress, IPAddress) { return true; }
  bool config(IPAddress, IPAddress, IPAddress, IPAddress = IPAddress(), IPAddress = IPAddress()) { return true; }
  IPAddress softAPIP() { return IPAddress(192, 168, 4, 1); }
  IPAddress localIP() { return IPAddress(0, 0, 0, 0); }   // AP mode: the station interface has no address (prints 0.0.0.0 like the real board)
  bool mode(int) { return true; }
};
inline WiFiClass WiFi;
