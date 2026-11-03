// BLE stub (ESP32 Arduino BLE API subset). The device name, service and characteristics (uuid + properties) are reported to JS as Board.ble.
// JS hook: sim.boards.x.bleConnect(), bleWrite(charUuid, "WEIGHT:500"), and
// TX notifications arrive as 'ble-tx' events. Notifications are dropped while no client is connected (like real hardware).
#pragma once
#ifdef VC_BLE_BRIDGE
#error "WiFi.h and BLEDevice.h both use the simulator's BLE bridge; a sketch with both is not supported"
#endif
#define VC_BLE_BRIDGE
#include "vc_core.h"
#include <vector>
class BLEServer; class BLECharacteristic; class BLEService; class BLE2902;
class BLECharacteristicCallbacks { public: virtual void onWrite(BLECharacteristic*) {} virtual void onRead(BLECharacteristic*) {} virtual ~BLECharacteristicCallbacks() {} };
class BLEServerCallbacks { public: virtual void onConnect(BLEServer*) {} virtual void onDisconnect(BLEServer*) {} virtual ~BLEServerCallbacks() {} };
class BLE2902 { public: void setNotifications(bool) {} };
class BLEAdvertising { public: void start() {} void stop() {} void addServiceUUID(const char*) {} void setScanResponse(bool) {} void setMinPreferred(int) {} };
class BLECharacteristic {
 public:
  static const uint32_t PROPERTY_READ = 1, PROPERTY_WRITE = 2, PROPERTY_NOTIFY = 4, PROPERTY_INDICATE = 8, PROPERTY_WRITE_NR = 16;
  std::string uuid; uint32_t props; String value; BLECharacteristicCallbacks* cb = nullptr; bool* connected = nullptr;
  BLECharacteristic(const char* u, uint32_t p) : uuid(u), props(p) {}
  void addDescriptor(BLE2902*) {}
  void setCallbacks(BLECharacteristicCallbacks* c) { cb = c; }
  String getValue() { return value; }
  void setValue(const String& v) { value = v; }
  void setValue(const char* v) { value = String(v); }
  void setValue(const std::string& v) { value = String(v); }
  void notify() {
    vc_advance(200);  // radio/stack work
    if (connected && *connected) { vc_js_ble_notify(uuid.c_str(), (const uint8_t*)value.c_str(), value.length(), vc_now()); vc_activity(); }
    else vc_js_hook("ble.notify.dropped", 0, 0, vc_now());
  }
};
class BLEService {
 public:
  bool* connected = nullptr;
  BLECharacteristic* createCharacteristic(const char* u, uint32_t p);
  void start() {}
};
class BLEServer {
 public:
  BLEServerCallbacks* cb = nullptr; bool connected = false; BLEAdvertising adv;
  void setCallbacks(BLEServerCallbacks* c) { cb = c; }
  BLEService* createService(const char* uuid) { auto* s = new BLEService(); s->connected = &connected; vc_js_hook((std::string("ble.service:") + uuid).c_str(), 0, 0, vc_now()); return s; }
  BLEAdvertising* getAdvertising() { return &adv; }
  void startAdvertising() {}
};
inline std::vector<BLECharacteristic*>& vc_ble_chars() { static std::vector<BLECharacteristic*> v; return v; }
inline BLEServer*& vc_ble_server() { static BLEServer* s = nullptr; return s; }
inline BLECharacteristic* BLEService::createCharacteristic(const char* u, uint32_t p) {
  auto* c = new BLECharacteristic(u, p); c->connected = connected; vc_ble_chars().push_back(c);
  vc_js_hook((std::string("ble.char:") + u).c_str(), (int)p, 0, vc_now());   // runtime: Board.ble.chars[uuid] = props
  return c;
}
class BLEDevice {
 public:
  static void init(const char* name) { vc_js_hook((std::string("ble.name:") + name).c_str(), 0, 0, vc_now()); }   // runtime: Board.ble.name
  static BLEServer* createServer() { if (!vc_ble_server()) vc_ble_server() = new BLEServer(); return vc_ble_server(); }
};
inline void vc_ble_mail(const VcMsg& m) {
  BLEServer* s = vc_ble_server();
  if (m.type == 3 || m.type == 4) {
    if (!s) return;
    s->connected = (m.type == 3);
    if (s->cb) { if (m.type == 3) s->cb->onConnect(s); else s->cb->onDisconnect(s); }
    return;
  }
  for (auto* c : vc_ble_chars())
    if (c->uuid == m.uuid) { c->value = String(std::string((const char*)m.data, m.len)); if (c->cb) c->cb->onWrite(c); return; }
}
inline int vc_ble_reg = (vc_register_mail(2, vc_ble_mail), vc_register_mail(3, vc_ble_mail), vc_register_mail(4, vc_ble_mail), 0);
