#include <windows.h>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <string>
#include <vector>

#include "GUI/dlpc350_firmware.h"

namespace fs = std::filesystem;

namespace {

std::vector<unsigned char> read_binary(const fs::path& path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) throw std::runtime_error("Could not open input file");
    const std::streamsize size = input.tellg();
    if (size <= 0) throw std::runtime_error("Input file is empty");
    input.seekg(0);
    std::vector<unsigned char> data(static_cast<size_t>(size));
    if (!input.read(reinterpret_cast<char*>(data.data()), size)) {
        throw std::runtime_error("Could not read input file");
    }
    return data;
}

void write_binary(const fs::path& path, const unsigned char* data, size_t size) {
    if (!path.parent_path().empty()) fs::create_directories(path.parent_path());
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output || !output.write(reinterpret_cast<const char*>(data), size)) {
        throw std::runtime_error("Could not write output firmware");
    }
}

int pattern_id_from_name(const fs::path& path) {
    std::wsmatch match;
    const std::wstring name = path.filename().wstring();
    if (!std::regex_search(name, match, std::wregex(LR"(^(\d+))"))) return -1;
    return std::stoi(match[1].str());
}

std::vector<fs::path> collect_patterns(const fs::path& folder) {
    std::vector<fs::path> indexed(22);
    for (const auto& entry : fs::directory_iterator(folder)) {
        if (!entry.is_regular_file() || entry.path().extension() != L".bmp") continue;
        const int pattern_id = pattern_id_from_name(entry.path());
        if (pattern_id >= 0 && pattern_id < static_cast<int>(indexed.size())) {
            if (!indexed[static_cast<size_t>(pattern_id)].empty()) {
                throw std::runtime_error("Duplicate BMP for pattern id " +
                                         std::to_string(pattern_id));
            }
            indexed[static_cast<size_t>(pattern_id)] = entry.path();
        }
    }
    for (size_t index = 0; index < indexed.size(); ++index) {
        if (indexed[index].empty()) {
            throw std::runtime_error("Missing BMP for pattern id " + std::to_string(index));
        }
    }
    return indexed;
}

void validate_bmp(const std::vector<unsigned char>& data) {
    if (data.size() < 54 || data[0] != 'B' || data[1] != 'M') {
        throw std::runtime_error("Pattern is not a BMP file");
    }
    const auto read_u16 = [&data](size_t offset) {
        return static_cast<uint16_t>(static_cast<uint16_t>(data[offset]) |
                                     (static_cast<uint16_t>(data[offset + 1]) << 8));
    };
    const auto read_u32 = [&data](size_t offset) {
        return static_cast<uint32_t>(data[offset]) |
               (static_cast<uint32_t>(data[offset + 1]) << 8) |
               (static_cast<uint32_t>(data[offset + 2]) << 16) |
               (static_cast<uint32_t>(data[offset + 3]) << 24);
    };
    const uint32_t width = read_u32(18);
    const uint32_t height = read_u32(22);
    const uint16_t bits = read_u16(28);
    if (width != 912 || height != 1140 || bits != 24) {
        throw std::runtime_error("Flash patterns must be 912x1140 24-bit BMP files");
    }
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    fs::path base_firmware;
    fs::path pattern_folder;
    fs::path output_firmware;
    for (int index = 1; index < argc; ++index) {
        const std::wstring arg = argv[index];
        if (arg == L"--base" && index + 1 < argc) base_firmware = argv[++index];
        else if (arg == L"--patterns" && index + 1 < argc) pattern_folder = argv[++index];
        else if (arg == L"--output" && index + 1 < argc) output_firmware = argv[++index];
        else {
            std::wcerr << L"Unknown or incomplete argument: " << arg << L"\n";
            return 2;
        }
    }

    if (base_firmware.empty() || pattern_folder.empty() || output_firmware.empty()) {
        std::wcerr << L"Usage: dlpc350_firmware_pack.exe --base firmware.bin "
                      L"--patterns generated_patterns_flash --output package.bin\n";
        return 2;
    }

    try {
        std::wcout << L"[pack] Reading base firmware: " << base_firmware << L"\n";
        std::vector<unsigned char> firmware = read_binary(base_firmware);
        const int verify = DLPC350_Frmw_CopyAndVerifyImage(
            firmware.data(), static_cast<int>(firmware.size()));
        if (verify < 0) {
            throw std::runtime_error("Base firmware validation failed (code " +
                                     std::to_string(verify) + ")");
        }

        const std::vector<fs::path> patterns = collect_patterns(pattern_folder);
        if (DLPC350_Frmw_SPLASH_InitBuffer(static_cast<int>(patterns.size())) < 0) {
            throw std::runtime_error("Could not initialize splash image buffer");
        }

        for (size_t index = 0; index < patterns.size(); ++index) {
            std::vector<unsigned char> bitmap = read_binary(patterns[index]);
            validate_bmp(bitmap);
            uint8 compression = SPLASH_NOCOMP_SPECIFIED;
            uint32 compressed_size = 0;
            const int result = DLPC350_Frmw_SPLASH_AddSplash(
                bitmap.data(), &compression, &compressed_size);
            if (result < 0) {
                throw std::runtime_error("Could not add pattern " + std::to_string(index) +
                                         " (code " + std::to_string(result) + ")");
            }
            std::wcout << L"[pack] Pattern " << index << L" "
                       << patterns[index].filename() << L" -> " << compressed_size
                       << L" bytes, compression=" << static_cast<int>(compression) << L"\n";
        }

        unsigned char* output_data = nullptr;
        uint32 output_size = 0;
        DLPC350_Frmw_Get_NewFlashImage(&output_data, &output_size);
        if (!output_data || output_size == 0 || output_size > MAX_FIRMWARE_BYTES) {
            throw std::runtime_error("Packed firmware exceeds the safe 30 MB limit");
        }
        write_binary(output_firmware, output_data, output_size);
        std::wcout << L"[pack] READY " << output_firmware << L" (" << output_size
                   << L" bytes, 22 splash images)\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "[pack] ERROR: " << error.what() << "\n";
        return 1;
    }
}
