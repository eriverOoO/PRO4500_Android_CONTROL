#include <cstdio>
#include <string>
#include <vector>

#include <windows.h>

#include "GUI/dlpc350_common.h"
#include "GUI/dlpc350_api.h"
#include "GUI/dlpc350_usb.h"
#include "projector_usb_diagnostics.h"

namespace {

std::string utf8(const std::wstring& value) {
    if (value.empty()) {
        return "";
    }

    const int length = WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, nullptr, 0, nullptr, nullptr);
    if (length <= 0) {
        return "";
    }

    std::vector<char> buffer(static_cast<size_t>(length));
    WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, buffer.data(), length, nullptr, nullptr);
    return std::string(buffer.data());
}

}  // namespace

int main() {
    if (DLPC350_USB_Init() != 0) {
        std::printf("usb init: FAIL\n");
        return 1;
    }

    if (DLPC350_USB_Open() != 0) {
        std::printf("usb open: FAIL\n");
        std::printf("%s\n", utf8(DLPC350_USB_LastError()).c_str());
        DLPC350_USB_Exit();
        return 2;
    }

    std::printf("usb open: OK\n");

    unsigned int app = 0;
    unsigned int api = 0;
    unsigned int sw = 0;
    unsigned int seq = 0;
    const int version_result = DLPC350_GetVersion(&app, &api, &sw, &seq);

    DLPC350_USB_Close();
    DLPC350_USB_Exit();

    if (version_result != 0) {
        std::printf("get version: FAIL\n");
        return 3;
    }

    std::printf("get version: OK\n");
    std::printf("app=0x%08x api=0x%08x sw=0x%08x seq=0x%08x\n", app, api, sw, seq);
    return 0;
}
