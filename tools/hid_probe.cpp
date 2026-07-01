#include <cstdio>
#include <string>
#include <vector>

#include <windows.h>

#include "hidapi.h"

namespace {

std::string utf8(const wchar_t* value) {
    if (!value || !*value) {
        return "";
    }

    const int length = WideCharToMultiByte(CP_UTF8, 0, value, -1, nullptr, 0, nullptr, nullptr);
    if (length <= 0) {
        return "";
    }

    std::vector<char> buffer(static_cast<size_t>(length));
    WideCharToMultiByte(CP_UTF8, 0, value, -1, buffer.data(), length, nullptr, nullptr);
    return std::string(buffer.data());
}

void print_device(const hid_device_info* device) {
    std::printf("VID:PID      %04hx:%04hx\n", device->vendor_id, device->product_id);
    std::printf("interface    %d\n", device->interface_number);
    std::printf("usage        0x%04hx:0x%04hx\n", device->usage_page, device->usage);
    std::printf("manufacturer %s\n", utf8(device->manufacturer_string).c_str());
    std::printf("product      %s\n", utf8(device->product_string).c_str());
    std::printf("serial       %s\n", utf8(device->serial_number).c_str());
    std::printf("path         %s\n", device->path ? device->path : "");
}

void try_create_file(const char* label, const char* path, DWORD access, DWORD share) {
    SetLastError(ERROR_SUCCESS);
    HANDLE handle = CreateFileA(
        path,
        access,
        share,
        nullptr,
        OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED,
        nullptr);

    if (handle == INVALID_HANDLE_VALUE) {
        std::printf("%-12s FAIL error=%lu\n", label, GetLastError());
        return;
    }

    std::printf("%-12s OK\n", label);
    CloseHandle(handle);
}

void probe_create_file_modes(const char* path) {
    try_create_file("cf enum", path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE);
    try_create_file("cf hidapi", path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ);
    try_create_file("cf rw/rw", path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE);
    try_create_file("cf read/rw", path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE);
    try_create_file("cf write/rw", path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE);
}

}  // namespace

int main() {
    constexpr unsigned short kVid = 0x0451;
    constexpr unsigned short kPid = 0x6401;

    if (hid_init() != 0) {
        std::printf("hid_init: FAIL\n");
        return 1;
    }

    hid_device_info* devices = hid_enumerate(kVid, 0);
    int ti_devices = 0;
    int target_devices = 0;
    int opened_devices = 0;

    for (hid_device_info* device = devices; device; device = device->next) {
        ++ti_devices;
        std::printf("---- TI HID device %d ----\n", ti_devices);
        print_device(device);

        if (device->product_id == kPid) {
            ++target_devices;
            hid_device* handle = hid_open_path(device->path);
            if (handle) {
                ++opened_devices;
                std::printf("open         OK\n");
                hid_close(handle);
            } else {
                std::printf("open         FAIL\n");
            }
            probe_create_file_modes(device->path);
        } else {
            std::printf("open         skipped (PID is not 6401)\n");
        }
        std::printf("\n");
    }

    hid_free_enumeration(devices);
    hid_exit();

    std::printf("summary      ti=%d target=%d opened=%d\n", ti_devices, target_devices, opened_devices);

    if (target_devices == 0) {
        return 2;
    }
    if (opened_devices == 0) {
        return 3;
    }
    return 0;
}
