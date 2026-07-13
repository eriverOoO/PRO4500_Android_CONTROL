#include <windows.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "GUI/dlpc350_common.h"
#include "GUI/dlpc350_api.h"
#include "GUI/dlpc350_firmware.h"
#include "GUI/dlpc350_usb.h"

namespace fs = std::filesystem;

namespace {

constexpr unsigned int kBootloaderBytes = 128 * 1024;
constexpr unsigned int kSectorBytes = 128 * 1024;
constexpr unsigned int kExpectedManufacturer = 0x20;
constexpr unsigned long long kExpectedDevice = 0x227e;

class UsbConnection {
public:
    bool open() {
        close();
        if (DLPC350_USB_Init() != 0) return false;
        initialized_ = true;
        if (DLPC350_USB_Open() != 0) {
            close();
            return false;
        }
        opened_ = true;
        return true;
    }

    bool reopen_for_bootloader(int timeout_seconds) {
        close();
        const auto deadline = std::chrono::steady_clock::now() +
                              std::chrono::seconds(timeout_seconds);
        while (std::chrono::steady_clock::now() < deadline) {
            if (open()) return true;
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }
        return false;
    }

    void close() {
        if (opened_) DLPC350_USB_Close();
        if (initialized_) DLPC350_USB_Exit();
        opened_ = false;
        initialized_ = false;
    }

    ~UsbConnection() { close(); }

private:
    bool initialized_ = false;
    bool opened_ = false;
};

bool wait_for_mode(bool structured_light) {
    for (int attempt = 0; attempt < 30; ++attempt) {
        bool mode = false;
        if (DLPC350_GetMode(&mode) == 0 && mode == structured_light) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    return false;
}

bool prepare_image_mode(unsigned int* image_count) {
    DLPC350_PatternDisplay(0);
    if (DLPC350_SetMode(false) < 0 || !wait_for_mode(false)) return false;
    if (DLPC350_SetLedEnables(false, false, false, true) < 0) return false;
    return DLPC350_GetNumImagesInFlash(image_count) >= 0;
}

bool show_image(unsigned int index, unsigned int image_count) {
    return index < image_count && DLPC350_LoadImageIndex(index) >= 0;
}

int run_session() {
    UsbConnection usb;
    if (!usb.open()) {
        std::cout << "ERROR projector_usb_open_failed\n" << std::flush;
        return 1;
    }
    unsigned int image_count = 0;
    if (!prepare_image_mode(&image_count)) {
        std::cout << "ERROR projector_image_mode_failed\n" << std::flush;
        return 1;
    }
    std::cout << "READY " << image_count << "\n" << std::flush;

    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream command(line);
        std::string action;
        command >> action;
        if (action == "show") {
            unsigned int index = 0;
            if (!(command >> index) || !show_image(index, image_count)) {
                std::cout << "ERROR show_failed\n" << std::flush;
            } else {
                std::cout << "OK show " << index << "\n" << std::flush;
            }
        } else if (action == "black") {
            if (!show_image(1, image_count)) {
                std::cout << "ERROR black_failed\n" << std::flush;
            } else {
                std::cout << "OK black\n" << std::flush;
            }
        } else if (action == "status") {
            std::cout << "OK images " << image_count << "\n" << std::flush;
        } else if (action == "quit") {
            std::cout << "OK quit\n" << std::flush;
            return 0;
        } else {
            std::cout << "ERROR unknown_command\n" << std::flush;
        }
    }
    return 0;
}

int run_status() {
    UsbConnection usb;
    if (!usb.open()) {
        std::cerr << "ERROR projector_usb_open_failed\n";
        return 1;
    }
    unsigned int image_count = 0;
    if (DLPC350_GetNumImagesInFlash(&image_count) < 0) {
        std::cerr << "ERROR projector_image_count_failed\n";
        return 1;
    }
    std::cout << "OK images " << image_count << "\n";
    return 0;
}

int run_stop() {
    UsbConnection usb;
    unsigned int image_count = 0;
    if (!usb.open() || !prepare_image_mode(&image_count) ||
        !show_image(1, image_count)) {
        std::cerr << "ERROR projector_stop_failed\n";
        return 1;
    }
    std::cout << "OK stopped black=1\n";
    return 0;
}

int run_auto(unsigned int count, unsigned int exposure_us, unsigned int period_us, bool repeat) {
    if (count == 0 || count > 64 || exposure_us < 8333 || period_us < exposure_us ||
        (period_us != exposure_us && period_us - exposure_us <= 230)) {
        std::cerr << "ERROR invalid_auto_timing_or_count\n";
        return 2;
    }

    UsbConnection usb;
    if (!usb.open()) {
        std::cerr << "ERROR projector_usb_open_failed\n";
        return 1;
    }
    unsigned int image_count = 0;
    if (DLPC350_GetNumImagesInFlash(&image_count) < 0 || image_count < count) {
        std::cerr << "ERROR not_enough_flash_images\n";
        return 1;
    }

    if (DLPC350_PatternDisplay(0) < 0 || DLPC350_SetMode(true) < 0 ||
        !wait_for_mode(true) || DLPC350_SetPatternDisplayMode(false) < 0 ||
        DLPC350_SetLedEnables(true, false, false, true) < 0 ||
        DLPC350_SetPatternTriggerMode(1) < 0 ||
        DLPC350_SetPatternConfig(count, repeat, count, count) < 0 ||
        DLPC350_SetExposure_FramePeriod(exposure_us, period_us) < 0) {
        std::cerr << "ERROR auto_configuration_failed\n";
        return 1;
    }

    DLPC350_ClearPatLut();
    std::vector<unsigned char> image_lut(count);
    for (unsigned int index = 0; index < count; ++index) {
        image_lut[index] = static_cast<unsigned char>(index);
        if (DLPC350_AddToPatLut(0, 0, 8, 4, false, false, true, false) < 0) {
            std::cerr << "ERROR pattern_lut_build_failed\n";
            return 1;
        }
    }
    if (DLPC350_SendPatLut() < 0 ||
        DLPC350_SendImageLut(image_lut.data(), count) < 0) {
        std::cerr << "ERROR lut_upload_failed\n";
        return 1;
    }
    unsigned int validation = 0;
    if (DLPC350_ValidatePatLutData(&validation) < 0 || (validation & 0x03) != 0 ||
        DLPC350_PatternDisplay(2) < 0) {
        std::cerr << "ERROR lut_validation_or_start_failed status=" << validation << "\n";
        return 1;
    }
    std::cout << "OK auto_started count=" << count << " exposure_us=" << exposure_us
              << " period_us=" << period_us << " repeat=" << (repeat ? 1 : 0) << "\n";
    return 0;
}

std::vector<unsigned char> read_firmware(const fs::path& path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) throw std::runtime_error("could_not_open_firmware");
    const std::streamsize size = input.tellg();
    if (size <= static_cast<std::streamsize>(kBootloaderBytes) ||
        size > static_cast<std::streamsize>(MAX_FIRMWARE_BYTES)) {
        throw std::runtime_error("invalid_firmware_size");
    }
    input.seekg(0);
    std::vector<unsigned char> data(static_cast<size_t>(size));
    if (!input.read(reinterpret_cast<char*>(data.data()), size)) {
        throw std::runtime_error("could_not_read_firmware");
    }
    if (DLPC350_Frmw_CopyAndVerifyImage(data.data(), static_cast<int>(data.size())) < 0) {
        throw std::runtime_error("invalid_firmware_image");
    }
    return data;
}

int run_flash(const fs::path& firmware_path, const std::wstring& confirmation) {
    if (confirmation != L"ERASE_APP_FLASH") {
        std::cerr << "ERROR confirmation_token_required\n";
        return 2;
    }

    try {
        std::vector<unsigned char> firmware = read_firmware(firmware_path);
        UsbConnection usb;
        if (!usb.open() || DLPC350_EnterProgrammingMode() < 0) {
            std::cerr << "ERROR enter_programming_mode_failed\n";
            return 1;
        }
        std::cout << "[flash] Waiting for bootloader USB mode...\n" << std::flush;
        std::this_thread::sleep_for(std::chrono::seconds(2));
        if (!usb.reopen_for_bootloader(30)) {
            std::cerr << "ERROR bootloader_reconnect_failed\n";
            return 1;
        }

        unsigned short manufacturer = 0;
        unsigned long long device = 0;
        if (DLPC350_GetFlashManID(&manufacturer) < 0 ||
            DLPC350_GetFlashDevID(&device) < 0 || manufacturer != kExpectedManufacturer ||
            (device & 0xffff) != kExpectedDevice) {
            std::cerr << "ERROR unsupported_flash_device manufacturer=0x" << std::hex
                      << manufacturer << " device=0x" << (device & 0xffff) << "\n";
            return 1;
        }

        const unsigned int last_byte = static_cast<unsigned int>(firmware.size() - 1);
        const unsigned int first_sector = kBootloaderBytes / kSectorBytes;
        const unsigned int last_sector = last_byte / kSectorBytes;
        if (DLPC350_SetFlashType(0) < 0) {
            std::cerr << "ERROR set_flash_type_failed\n";
            return 1;
        }
        for (unsigned int sector = first_sector; sector <= last_sector; ++sector) {
            const unsigned int address = sector * kSectorBytes;
            std::cout << "[flash] Erasing 0x" << std::hex << address << std::dec << "\n"
                      << std::flush;
            if (DLPC350_SetFlashAddr(address) < 0 || DLPC350_FlashSectorErase() < 0) {
                std::cerr << "ERROR sector_erase_failed\n";
                return 1;
            }
            DLPC350_WaitForFlashReady();
        }

        unsigned int remaining = static_cast<unsigned int>(firmware.size()) - kBootloaderBytes;
        if (DLPC350_SetFlashAddr(kBootloaderBytes) < 0 ||
            DLPC350_SetUploadSize(remaining) < 0) {
            std::cerr << "ERROR flash_upload_setup_failed\n";
            return 1;
        }
        unsigned int offset = kBootloaderBytes;
        int last_percent = -1;
        while (remaining > 0) {
            const int sent = DLPC350_UploadData(firmware.data() + offset, remaining);
            if (sent <= 0) {
                std::cerr << "ERROR flash_upload_failed\n";
                return 1;
            }
            offset += static_cast<unsigned int>(sent);
            remaining -= static_cast<unsigned int>(sent);
            const int percent = static_cast<int>(100ULL * (offset - kBootloaderBytes) /
                                                 (firmware.size() - kBootloaderBytes));
            if (percent != last_percent && percent % 5 == 0) {
                std::cout << "[flash] " << percent << "%\n" << std::flush;
                last_percent = percent;
            }
        }
        DLPC350_WaitForFlashReady();

        unsigned int expected_checksum = 0;
        for (size_t index = kBootloaderBytes; index < firmware.size(); ++index) {
            expected_checksum += firmware[index];
        }
        unsigned int flash_checksum = 0;
        const unsigned long application_size =
            static_cast<unsigned long>(firmware.size() - kBootloaderBytes);
        std::cout << "[flash] Verifying checksum...\n" << std::flush;
        if (DLPC350_SetFlashAddr(kBootloaderBytes) < 0 ||
            DLPC350_SetUploadSize(application_size) < 0 ||
            DLPC350_CalculateFlashChecksum() < 0) {
            DLPC350_ExitProgrammingMode();
            std::cerr << "ERROR flash_checksum_setup_failed\n";
            return 1;
        }
        DLPC350_WaitForFlashReady();
        if (DLPC350_GetFlashChecksum(&flash_checksum) < 0 ||
            flash_checksum != expected_checksum) {
            DLPC350_ExitProgrammingMode();
            std::cerr << "ERROR flash_checksum_mismatch expected=0x" << std::hex
                      << expected_checksum << " actual=0x" << flash_checksum << "\n";
            return 1;
        }
        if (DLPC350_ExitProgrammingMode() < 0) {
            std::cerr << "ERROR exit_programming_mode_failed\n";
            return 1;
        }
        std::cout << "OK flash_complete bootloader_preserved=1 checksum=0x" << std::hex
                  << flash_checksum << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "ERROR " << error.what() << "\n";
        return 1;
    }
}

unsigned int parse_uint(const wchar_t* value, const char* label) {
    try {
        return static_cast<unsigned int>(std::stoul(value));
    } catch (...) {
        throw std::runtime_error(std::string("invalid_") + label);
    }
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        std::cerr << "Usage: dlpc350_projector.exe session|status|stop|auto|flash\n";
        return 2;
    }
    try {
        const std::wstring command = argv[1];
        if (command == L"session") return run_session();
        if (command == L"status") return run_status();
        if (command == L"stop") return run_stop();
        if (command == L"auto") {
            unsigned int count = 22;
            unsigned int exposure = 500000;
            unsigned int period = 500000;
            bool repeat = true;
            for (int index = 2; index < argc; ++index) {
                const std::wstring arg = argv[index];
                if (arg == L"--count" && index + 1 < argc) count = parse_uint(argv[++index], "count");
                else if (arg == L"--exposure-us" && index + 1 < argc) exposure = parse_uint(argv[++index], "exposure");
                else if (arg == L"--period-us" && index + 1 < argc) period = parse_uint(argv[++index], "period");
                else if (arg == L"--once") repeat = false;
                else throw std::runtime_error("invalid_auto_argument");
            }
            return run_auto(count, exposure, period, repeat);
        }
        if (command == L"flash") {
            fs::path firmware;
            std::wstring confirmation;
            for (int index = 2; index < argc; ++index) {
                const std::wstring arg = argv[index];
                if (arg == L"--firmware" && index + 1 < argc) firmware = argv[++index];
                else if (arg == L"--confirm" && index + 1 < argc) confirmation = argv[++index];
                else throw std::runtime_error("invalid_flash_argument");
            }
            if (firmware.empty()) throw std::runtime_error("firmware_path_required");
            return run_flash(firmware, confirmation);
        }
        throw std::runtime_error("unknown_command");
    } catch (const std::exception& error) {
        std::cerr << "ERROR " << error.what() << "\n";
        return 2;
    }
}
