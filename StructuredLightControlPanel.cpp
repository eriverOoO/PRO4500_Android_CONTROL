#define UNICODE
#define _UNICODE

#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <commctrl.h>
#include <shlobj.h>
#include <shellapi.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "GUI/dlpc350_common.h"
#include "GUI/dlpc350_api.h"
#include "GUI/dlpc350_usb.h"

namespace {

constexpr wchar_t kAppClass[] = L"StructuredLightControlPanelWindow";
constexpr UINT WM_APP_LOG = WM_APP + 1;
constexpr UINT WM_APP_DONE = WM_APP + 2;

enum ControlId {
    IDC_STATUS = 100,
    IDC_PHONE_URL,
    IDC_PATTERNS,
    IDC_OUTPUT,
    IDC_HOST,
    IDC_PUBLIC_HOST,
    IDC_PORT,
    IDC_MONITOR,
    IDC_SETTLE,
    IDC_EXPOSURE,
    IDC_ISO,
    IDC_FOCUS,
    IDC_ANGLES,
    IDC_MANUAL,
    IDC_WINDOWED,
    IDC_STRETCH,
    IDC_PAUSE_FIRST,
    IDC_BIDIRECTIONAL_ANALYSIS,
    IDC_HDR_ENABLE,
    IDC_HDR_BRACKETS,
    IDC_MEASUREMENT_CHANNEL,
    IDC_RETAIN_RAW_EXPOSURES,
    IDC_RETAIN_HDR_MASKS,
    IDC_LOG,
    IDC_START,
    IDC_STOP,
    IDC_NEXT_ANGLE,
    IDC_INSTALL_APK,
    IDC_OPEN_OUTPUT,
    IDC_REFRESH_URL,
    IDC_BROWSE_PATTERNS,
    IDC_BROWSE_OUTPUT,
    IDC_LED_SLIDER,
    IDC_LED_VALUE,
    IDC_APPLY_LED,
    IDC_LED_OFF,
};

struct AppState {
    HINSTANCE instance{};
    HWND window{};
    HWND status{};
    HWND phoneUrl{};
    HWND patterns{};
    HWND output{};
    HWND host{};
    HWND publicHost{};
    HWND port{};
    HWND monitor{};
    HWND settle{};
    HWND exposure{};
    HWND iso{};
    HWND focus{};
    HWND angles{};
    HWND manual{};
    HWND windowed{};
    HWND stretch{};
    HWND pauseFirst{};
    HWND bidirectionalAnalysis{};
    HWND hdrEnable{};
    HWND hdrBrackets{};
    HWND measurementChannel{};
    HWND retainRawExposures{};
    HWND retainHdrMasks{};
    HWND log{};
    HWND start{};
    HWND stop{};
    HWND nextAngle{};
    HWND ledSlider{};
    HWND ledValue{};
    HWND applyLed{};
    HWND ledOff{};
    PROCESS_INFORMATION scanProcess{};
    HANDLE scanPipeRead = nullptr;
    std::atomic_bool scanRunning{false};
    std::wstring root;
    std::wstring angleAdvanceFile;
};

AppState g_app;
std::mutex g_usbMutex;

std::wstring quote(const std::wstring& value) {
    std::wstring out = L"\"";
    for (wchar_t ch : value) {
        if (ch == L'"') out += L'\\';
        out += ch;
    }
    out += L"\"";
    return out;
}

std::wstring get_exe_dir() {
    std::vector<wchar_t> buffer(MAX_PATH);
    DWORD len = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    while (len == buffer.size()) {
        buffer.resize(buffer.size() * 2);
        len = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    }
    std::wstring path(buffer.data(), len);
    size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"." : path.substr(0, slash);
}

std::wstring path_join(const std::wstring& a, const std::wstring& b) {
    if (a.empty()) return b;
    if (a.back() == L'\\' || a.back() == L'/') return a + b;
    return a + L"\\" + b;
}

std::wstring runtime_dir() {
    return path_join(g_app.root, L".runtime");
}

std::wstring angle_advance_file() {
    return path_join(runtime_dir(), L"angle_advance.signal");
}

std::wstring get_text(HWND hwnd) {
    int len = GetWindowTextLengthW(hwnd);
    std::wstring value(static_cast<size_t>(len), L'\0');
    GetWindowTextW(hwnd, value.data(), len + 1);
    return value;
}

void set_text(HWND hwnd, const std::wstring& value) {
    SetWindowTextW(hwnd, value.c_str());
}

std::wstring utf8_to_wide(const char* data, int len) {
    if (len <= 0) return L"";
    int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, data, len, nullptr, 0);
    UINT cp = CP_UTF8;
    DWORD flags = MB_ERR_INVALID_CHARS;
    if (size <= 0) {
        cp = CP_ACP;
        flags = 0;
        size = MultiByteToWideChar(cp, flags, data, len, nullptr, 0);
    }
    if (size <= 0) return L"";
    std::wstring out(static_cast<size_t>(size), L'\0');
    MultiByteToWideChar(cp, flags, data, len, out.data(), size);
    return out;
}

void post_log(const std::wstring& text) {
    PostMessageW(g_app.window, WM_APP_LOG, 0, reinterpret_cast<LPARAM>(new std::wstring(text)));
}

void append_log(HWND edit, const std::wstring& text) {
    int len = GetWindowTextLengthW(edit);
    SendMessageW(edit, EM_SETSEL, len, len);
    SendMessageW(edit, EM_REPLACESEL, FALSE, reinterpret_cast<LPARAM>(text.c_str()));
    SendMessageW(edit, EM_SCROLLCARET, 0, 0);
}

void set_status(const std::wstring& value) {
    set_text(g_app.status, value);
}

bool connect_projector(std::wstring& error) {
    if (DLPC350_USB_Init() != 0) {
        error = L"HIDAPI init failed";
        return false;
    }
    if (DLPC350_USB_Open() != 0) {
        DLPC350_USB_Exit();
        error = L"LightCrafter 4500 not found or cannot be opened (VID 0451, PID 6401)";
        return false;
    }
    return true;
}

void disconnect_projector() {
    DLPC350_USB_Close();
    DLPC350_USB_Exit();
}

bool set_blue_led(int brightness, std::wstring& error) {
    std::lock_guard<std::mutex> lock(g_usbMutex);
    if (!connect_projector(error)) {
        return false;
    }

    // Same rule as PRO4500.exe: DLPC350 LED current register is inverted.
    const unsigned char current = static_cast<unsigned char>(255 - std::clamp(brightness, 0, 255));
    const int enableResult = DLPC350_SetLedEnables(false, false, false, brightness > 0);
    const int currentResult = DLPC350_SetLedCurrents(255, 255, current);
    disconnect_projector();

    if (enableResult < 0 || currentResult < 0) {
        error = L"Blue LED command failed";
        return false;
    }
    return true;
}

void update_led_value_label() {
    const int value = static_cast<int>(SendMessageW(g_app.ledSlider, TBM_GETPOS, 0, 0));
    set_text(g_app.ledValue, std::to_wstring(value));
}

void apply_led_value(int value) {
    SendMessageW(g_app.ledSlider, TBM_SETPOS, TRUE, value);
    update_led_value_label();

    std::wstring error;
    if (set_blue_led(value, error)) {
        set_status(value > 0 ? L"Blue LED applied" : L"Blue LED off");
        append_log(g_app.log, L"\r\n[led] Blue LED brightness " + std::to_wstring(value) + L" applied.\r\n");
    } else {
        set_status(L"LED Error");
        append_log(g_app.log, L"\r\n[led] " + error + L"\r\n");
    }
}

void handle_scan_log(const std::wstring& text) {
    append_log(g_app.log, text);
    if (text.find(L"[angle] Waiting") != std::wstring::npos) {
        EnableWindow(g_app.nextAngle, TRUE);
        set_status(L"Waiting Angle");
    }
    if (text.find(L"[angle] Continue") != std::wstring::npos) {
        EnableWindow(g_app.nextAngle, FALSE);
        set_status(L"Running");
    }
}

std::wstring guess_lan_ip() {
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return L"127.0.0.1";

    SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock == INVALID_SOCKET) {
        WSACleanup();
        return L"127.0.0.1";
    }

    sockaddr_in remote{};
    remote.sin_family = AF_INET;
    remote.sin_port = htons(80);
    InetPtonW(AF_INET, L"8.8.8.8", &remote.sin_addr);
    connect(sock, reinterpret_cast<sockaddr*>(&remote), sizeof(remote));

    sockaddr_in local{};
    int localLen = sizeof(local);
    std::wstring result = L"127.0.0.1";
    if (getsockname(sock, reinterpret_cast<sockaddr*>(&local), &localLen) == 0) {
        wchar_t text[64]{};
        if (InetNtopW(AF_INET, &local.sin_addr, text, 64)) result = text;
    }
    closesocket(sock);
    WSACleanup();
    return result;
}

void refresh_phone_url() {
    std::wstring ip = get_text(g_app.publicHost);
    if (ip.empty()) {
        ip = guess_lan_ip();
        set_text(g_app.publicHost, ip);
    }
    std::wstring url = L"ws://" + ip + L":" + get_text(g_app.port) + L"/ws";
    set_text(g_app.phoneUrl, url);
}

HWND make_label(HWND parent, const wchar_t* text, int x, int y, int w, int h) {
    return CreateWindowW(L"STATIC", text, WS_CHILD | WS_VISIBLE, x, y, w, h, parent, nullptr, g_app.instance, nullptr);
}

HWND make_edit(HWND parent, int id, const std::wstring& text, int x, int y, int w, int h) {
    HWND hwnd = CreateWindowExW(
        WS_EX_CLIENTEDGE, L"EDIT", text.c_str(),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL,
        x, y, w, h, parent, reinterpret_cast<HMENU>(id), g_app.instance, nullptr);
    SendMessageW(hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
    return hwnd;
}

HWND make_button(HWND parent, int id, const wchar_t* text, int x, int y, int w, int h) {
    HWND hwnd = CreateWindowW(
        L"BUTTON", text, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        x, y, w, h, parent, reinterpret_cast<HMENU>(id), g_app.instance, nullptr);
    SendMessageW(hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
    return hwnd;
}

HWND make_checkbox(HWND parent, int id, const wchar_t* text, int x, int y, int w, int h, bool checked) {
    HWND hwnd = CreateWindowW(
        L"BUTTON", text, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX,
        x, y, w, h, parent, reinterpret_cast<HMENU>(id), g_app.instance, nullptr);
    SendMessageW(hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
    SendMessageW(hwnd, BM_SETCHECK, checked ? BST_CHECKED : BST_UNCHECKED, 0);
    return hwnd;
}

void set_font_recursive(HWND parent) {
    HFONT font = reinterpret_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
    SendMessageW(parent, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
}

std::wstring browse_folder(HWND owner, const wchar_t* title, const std::wstring& current) {
    BROWSEINFOW bi{};
    bi.hwndOwner = owner;
    bi.lpszTitle = title;
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    PIDLIST_ABSOLUTE pidl = SHBrowseForFolderW(&bi);
    if (!pidl) return current;
    wchar_t path[MAX_PATH]{};
    std::wstring result = current;
    if (SHGetPathFromIDListW(pidl, path)) result = path;
    CoTaskMemFree(pidl);
    return result;
}

bool file_exists(const std::wstring& path) {
    DWORD attrs = GetFileAttributesW(path.c_str());
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

bool dir_exists(const std::wstring& path) {
    DWORD attrs = GetFileAttributesW(path.c_str());
    return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY);
}

long long current_epoch_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

bool write_text_file(const std::wstring& path, const std::string& text) {
    HANDLE file = CreateFileW(
        path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;

    DWORD written = 0;
    BOOL ok = WriteFile(
        file, text.data(), static_cast<DWORD>(text.size()), &written, nullptr);
    CloseHandle(file);
    return ok && written == text.size();
}

void signal_next_angle() {
    if (!g_app.scanRunning.load()) return;
    if (g_app.angleAdvanceFile.empty()) {
        g_app.angleAdvanceFile = angle_advance_file();
    }
    CreateDirectoryW(runtime_dir().c_str(), nullptr);
    std::string token = std::to_string(current_epoch_ms());
    if (write_text_file(g_app.angleAdvanceFile, token)) {
        append_log(g_app.log, L"\r\n[ui] Next Angle signal sent. Continue after PCB rotation.\r\n");
        EnableWindow(g_app.nextAngle, FALSE);
        set_status(L"Running");
    } else {
        append_log(g_app.log, L"\r\n[ui] Failed to send Next Angle signal.\r\n");
    }
}

std::wstring checkbox_arg(HWND hwnd) {
    return SendMessageW(hwnd, BM_GETCHECK, 0, 0) == BST_CHECKED ? L"true" : L"false";
}

std::wstring build_scan_command() {
    std::wstring python = path_join(g_app.root, L".venv-pc\\Scripts\\python.exe");
    std::wstring controller = path_join(g_app.root, L"structured_light_pc_controller.py");

    std::wstringstream cmd;
    cmd << quote(python) << L" -u " << quote(controller)
        << L" --patterns " << quote(get_text(g_app.patterns))
        << L" --output " << quote(get_text(g_app.output))
        << L" --host " << quote(get_text(g_app.host))
        << L" --public-host " << quote(get_text(g_app.publicHost))
        << L" --port " << quote(get_text(g_app.port))
        << L" --monitor " << quote(get_text(g_app.monitor))
        << L" --settle-ms " << quote(get_text(g_app.settle))
        << L" --exposure-us " << quote(get_text(g_app.exposure))
        << L" --iso " << quote(get_text(g_app.iso))
        << L" --focus-diopters " << quote(get_text(g_app.focus))
        << L" --angles " << quote(get_text(g_app.angles))
        << L" --measurement-channel " << quote(get_text(g_app.measurementChannel))
        << L" --analysis-mode "
        << quote(SendMessageW(g_app.bidirectionalAnalysis, BM_GETCHECK, 0, 0) == BST_CHECKED ? L"bidirectional" : L"single")
        << L" --angle-advance-file " << quote(g_app.angleAdvanceFile)
        << L" --manual " << checkbox_arg(g_app.manual);

    if (SendMessageW(g_app.windowed, BM_GETCHECK, 0, 0) == BST_CHECKED) cmd << L" --windowed";
    if (SendMessageW(g_app.stretch, BM_GETCHECK, 0, 0) == BST_CHECKED) cmd << L" --stretch";
    if (SendMessageW(g_app.pauseFirst, BM_GETCHECK, 0, 0) == BST_CHECKED) cmd << L" --pause-before-first-angle";
    if (SendMessageW(g_app.hdrEnable, BM_GETCHECK, 0, 0) == BST_CHECKED) {
        cmd << L" --enable-hdr";
        std::wstring brackets = get_text(g_app.hdrBrackets);
        if (!brackets.empty()) cmd << L" --hdr-brackets " << quote(brackets);
    }
    if (SendMessageW(g_app.retainRawExposures, BM_GETCHECK, 0, 0) == BST_CHECKED) cmd << L" --retain-raw-exposures";
    if (SendMessageW(g_app.retainHdrMasks, BM_GETCHECK, 0, 0) == BST_CHECKED) cmd << L" --retain-hdr-masks";
    return cmd.str();
}

void read_pipe_thread(HANDLE pipe) {
    char buffer[4096];
    DWORD read = 0;
    while (ReadFile(pipe, buffer, sizeof(buffer), &read, nullptr) && read > 0) {
        post_log(utf8_to_wide(buffer, static_cast<int>(read)));
    }
    CloseHandle(pipe);
}

void wait_process_thread(HANDLE process) {
    WaitForSingleObject(process, INFINITE);
    DWORD exitCode = 0;
    GetExitCodeProcess(process, &exitCode);
    PostMessageW(g_app.window, WM_APP_DONE, exitCode, 0);
}

void start_scan() {
    if (g_app.scanRunning.load()) return;

    std::wstring python = path_join(g_app.root, L".venv-pc\\Scripts\\python.exe");
    std::wstring controller = path_join(g_app.root, L"structured_light_pc_controller.py");
    if (!file_exists(python)) {
        MessageBoxW(g_app.window, L".venv-pc\\Scripts\\python.exe was not found. Run prepare_pc_python_env.ps1 first.", L"Missing Python", MB_ICONERROR);
        return;
    }
    if (!file_exists(controller)) {
        MessageBoxW(g_app.window, L"structured_light_pc_controller.py was not found.", L"Missing Controller", MB_ICONERROR);
        return;
    }
    if (!dir_exists(get_text(g_app.patterns))) {
        MessageBoxW(g_app.window, L"Pattern folder does not exist.", L"Missing Patterns", MB_ICONERROR);
        return;
    }

    refresh_phone_url();
    CreateDirectoryW(runtime_dir().c_str(), nullptr);
    g_app.angleAdvanceFile = angle_advance_file();
    DeleteFileW(g_app.angleAdvanceFile.c_str());
    append_log(g_app.log, L"\r\n=== Started scan ===\r\nPhone app URL: " + get_text(g_app.phoneUrl) + L"\r\n");

    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = nullptr;
    HANDLE readPipe = nullptr;
    HANDLE writePipe = nullptr;
    if (!CreatePipe(&readPipe, &writePipe, &sa, 0)) {
        MessageBoxW(g_app.window, L"Failed to create output pipe.", L"Error", MB_ICONERROR);
        return;
    }
    SetHandleInformation(readPipe, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = writePipe;
    si.hStdError = writePipe;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);

    ZeroMemory(&g_app.scanProcess, sizeof(g_app.scanProcess));
    std::wstring cmd = build_scan_command();
    std::vector<wchar_t> mutableCmd(cmd.begin(), cmd.end());
    mutableCmd.push_back(L'\0');

    BOOL ok = CreateProcessW(
        nullptr, mutableCmd.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW,
        nullptr, g_app.root.c_str(), &si, &g_app.scanProcess);
    CloseHandle(writePipe);

    if (!ok) {
        CloseHandle(readPipe);
        MessageBoxW(g_app.window, L"Failed to start structured_light_pc_controller.py.", L"Error", MB_ICONERROR);
        return;
    }

    g_app.scanPipeRead = readPipe;
    g_app.scanRunning.store(true);
    EnableWindow(g_app.start, FALSE);
    EnableWindow(g_app.stop, TRUE);
    EnableWindow(g_app.nextAngle, FALSE);
    set_status(L"Running");
    std::thread(read_pipe_thread, readPipe).detach();
    std::thread(wait_process_thread, g_app.scanProcess.hProcess).detach();
}

void stop_scan() {
    if (!g_app.scanRunning.load()) return;
    if (MessageBoxW(g_app.window, L"Stop the running scan?", L"Stop Scan", MB_YESNO | MB_ICONQUESTION) != IDYES) return;
    TerminateProcess(g_app.scanProcess.hProcess, 130);
    EnableWindow(g_app.nextAngle, FALSE);
    set_status(L"Stopping");
}

void install_apk() {
    std::wstring adb = path_join(g_app.root, L".toolchains\\android-sdk\\platform-tools\\adb.exe");
    std::wstring apk = path_join(g_app.root, L"dist\\StructuredLightPhoneCamera-debug.apk");
    if (!file_exists(adb)) {
        MessageBoxW(g_app.window, L"ADB was not found. Run prepare_android_build_toolchain.ps1 first.", L"Missing ADB", MB_ICONERROR);
        return;
    }
    if (!file_exists(apk)) {
        MessageBoxW(g_app.window, L"APK was not found. Run build_phone_apk.bat first.", L"Missing APK", MB_ICONERROR);
        return;
    }
    append_log(g_app.log, L"\r\n=== Installing APK by USB ===\r\n");
    std::wstring cmd = quote(adb) + L" install -r " + quote(apk);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    std::vector<wchar_t> mutableCmd(cmd.begin(), cmd.end());
    mutableCmd.push_back(L'\0');
    if (CreateProcessW(nullptr, mutableCmd.data(), nullptr, nullptr, FALSE, 0, nullptr, g_app.root.c_str(), &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        append_log(g_app.log, L"ADB install command started.\r\n");
    } else {
        append_log(g_app.log, L"Failed to start ADB install.\r\n");
    }
}

void open_output() {
    std::wstring output = get_text(g_app.output);
    CreateDirectoryW(output.c_str(), nullptr);
    ShellExecuteW(g_app.window, L"open", output.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
}

void build_ui(HWND hwnd) {
    int margin = 14;
    int y = 12;
    make_label(hwnd, L"Status", margin, y + 4, 50, 22);
    g_app.status = make_label(hwnd, L"Idle", margin + 56, y + 4, 160, 22);
    make_label(hwnd, L"Phone URL", 430, y + 4, 80, 22);
    g_app.phoneUrl = make_edit(hwnd, IDC_PHONE_URL, L"", 510, y, 330, 24);
    EnableWindow(g_app.phoneUrl, FALSE);
    make_button(hwnd, IDC_REFRESH_URL, L"Refresh", 850, y, 90, 24);

    y += 34;
    make_label(hwnd, L"Blue LED", margin, y + 4, 70, 22);
    g_app.ledSlider = CreateWindowW(
        TRACKBAR_CLASSW, L"",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | TBS_HORZ | TBS_AUTOTICKS,
        90, y, 260, 32, hwnd, reinterpret_cast<HMENU>(IDC_LED_SLIDER), g_app.instance, nullptr);
    SendMessageW(g_app.ledSlider, TBM_SETRANGE, TRUE, MAKELPARAM(0, 255));
    SendMessageW(g_app.ledSlider, TBM_SETPOS, TRUE, 128);
    g_app.ledValue = make_label(hwnd, L"128", 360, y + 4, 45, 22);
    g_app.applyLed = make_button(hwnd, IDC_APPLY_LED, L"Apply LED", 425, y, 110, 28);
    g_app.ledOff = make_button(hwnd, IDC_LED_OFF, L"LED Off", 550, y, 90, 28);

    y += 42;
    make_label(hwnd, L"Patterns", margin, y + 4, 80, 22);
    g_app.patterns = make_edit(hwnd, IDC_PATTERNS, path_join(g_app.root, L"generated_patterns"), 110, y, 710, 24);
    make_button(hwnd, IDC_BROWSE_PATTERNS, L"Browse", 830, y, 110, 24);

    y += 32;
    make_label(hwnd, L"Output", margin, y + 4, 80, 22);
    g_app.output = make_edit(hwnd, IDC_OUTPUT, path_join(g_app.root, L"captures"), 110, y, 710, 24);
    make_button(hwnd, IDC_BROWSE_OUTPUT, L"Browse", 830, y, 110, 24);

    y += 38;
    make_label(hwnd, L"Host", margin, y + 4, 50, 22);
    g_app.host = make_edit(hwnd, IDC_HOST, L"0.0.0.0", 60, y, 120, 24);
    make_label(hwnd, L"Public IP", 200, y + 4, 75, 22);
    g_app.publicHost = make_edit(hwnd, IDC_PUBLIC_HOST, guess_lan_ip(), 275, y, 150, 24);
    make_label(hwnd, L"Port", 445, y + 4, 40, 22);
    g_app.port = make_edit(hwnd, IDC_PORT, L"8765", 485, y, 80, 24);
    make_label(hwnd, L"Monitor", 590, y + 4, 60, 22);
    g_app.monitor = make_edit(hwnd, IDC_MONITOR, L"1", 655, y, 60, 24);
    make_label(hwnd, L"Angles", 740, y + 4, 55, 22);
    g_app.angles = make_edit(hwnd, IDC_ANGLES, L"0,180", 795, y, 145, 24);

    y += 34;
    make_label(hwnd, L"Settle ms", margin, y + 4, 75, 22);
    g_app.settle = make_edit(hwnd, IDC_SETTLE, L"300", 90, y, 80, 24);
    make_label(hwnd, L"Exposure us", 200, y + 4, 90, 22);
    g_app.exposure = make_edit(hwnd, IDC_EXPOSURE, L"10000", 292, y, 100, 24);
    make_label(hwnd, L"ISO", 425, y + 4, 40, 22);
    g_app.iso = make_edit(hwnd, IDC_ISO, L"100", 465, y, 80, 24);
    make_label(hwnd, L"Focus", 575, y + 4, 50, 22);
    g_app.focus = make_edit(hwnd, IDC_FOCUS, L"0.0", 625, y, 90, 24);

    y += 34;
    g_app.hdrEnable = make_checkbox(hwnd, IDC_HDR_ENABLE, L"Enable HDR", margin, y, 110, 24, false);
    make_label(hwnd, L"HDR brackets", 130, y + 4, 100, 22);
    g_app.hdrBrackets = make_edit(hwnd, IDC_HDR_BRACKETS, L"short:3000:100,mid:10000:100,long:30000:100", 230, y, 500, 24);
    make_label(hwnd, L"Channel", 745, y + 4, 60, 22);
    g_app.measurementChannel = make_edit(hwnd, IDC_MEASUREMENT_CHANNEL, L"blue", 805, y, 90, 24);

    y += 34;
    g_app.retainRawExposures = make_checkbox(hwnd, IDC_RETAIN_RAW_EXPOSURES, L"Keep exposure originals", margin, y, 180, 24, false);
    g_app.retainHdrMasks = make_checkbox(hwnd, IDC_RETAIN_HDR_MASKS, L"Keep HDR masks", 210, y, 140, 24, false);

    y += 34;
    g_app.manual = make_checkbox(hwnd, IDC_MANUAL, L"Manual camera mode", margin, y, 160, 24, true);
    g_app.windowed = make_checkbox(hwnd, IDC_WINDOWED, L"Windowed projection", 190, y, 170, 24, false);
    g_app.stretch = make_checkbox(hwnd, IDC_STRETCH, L"Stretch patterns", 385, y, 140, 24, false);
    g_app.pauseFirst = make_checkbox(hwnd, IDC_PAUSE_FIRST, L"Pause before first angle", 550, y, 190, 24, false);
    g_app.bidirectionalAnalysis = make_checkbox(hwnd, IDC_BIDIRECTIONAL_ANALYSIS, L"Bidirectional analysis", 750, y, 190, 24, true);

    y += 42;
    g_app.start = make_button(hwnd, IDC_START, L"Start Scan", margin, y, 120, 32);
    g_app.stop = make_button(hwnd, IDC_STOP, L"Stop", 145, y, 90, 32);
    EnableWindow(g_app.stop, FALSE);
    g_app.nextAngle = make_button(hwnd, IDC_NEXT_ANGLE, L"Next Angle", 250, y, 120, 32);
    EnableWindow(g_app.nextAngle, FALSE);
    make_button(hwnd, IDC_INSTALL_APK, L"Install APK by USB", 385, y, 150, 32);
    make_button(hwnd, IDC_OPEN_OUTPUT, L"Open Output Folder", 550, y, 150, 32);

    y += 48;
    g_app.log = CreateWindowExW(
        WS_EX_CLIENTEDGE, L"EDIT", L"",
        WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_HSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_AUTOHSCROLL,
        margin, y, 926, 360, hwnd, reinterpret_cast<HMENU>(IDC_LOG), g_app.instance, nullptr);
    SendMessageW(g_app.log, WM_SETFONT, reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);

    refresh_phone_url();
}

LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    switch (msg) {
    case WM_CREATE:
        g_app.window = hwnd;
        build_ui(hwnd);
        return 0;
    case WM_COMMAND: {
        int id = LOWORD(wparam);
        switch (id) {
        case IDC_REFRESH_URL:
            refresh_phone_url();
            return 0;
        case IDC_BROWSE_PATTERNS:
            set_text(g_app.patterns, browse_folder(hwnd, L"Select pattern folder", get_text(g_app.patterns)));
            return 0;
        case IDC_BROWSE_OUTPUT:
            set_text(g_app.output, browse_folder(hwnd, L"Select output folder", get_text(g_app.output)));
            return 0;
        case IDC_START:
            start_scan();
            return 0;
        case IDC_STOP:
            stop_scan();
            return 0;
        case IDC_NEXT_ANGLE:
            signal_next_angle();
            return 0;
        case IDC_APPLY_LED:
            apply_led_value(static_cast<int>(SendMessageW(g_app.ledSlider, TBM_GETPOS, 0, 0)));
            return 0;
        case IDC_LED_OFF:
            apply_led_value(0);
            return 0;
        case IDC_INSTALL_APK:
            install_apk();
            return 0;
        case IDC_OPEN_OUTPUT:
            open_output();
            return 0;
        case IDC_PUBLIC_HOST:
        case IDC_PORT:
            if (HIWORD(wparam) == EN_CHANGE) refresh_phone_url();
            return 0;
        default:
            break;
        }
        break;
    }
    case WM_HSCROLL:
        if (reinterpret_cast<HWND>(lparam) == g_app.ledSlider) {
            update_led_value_label();
            return 0;
        }
        break;
    case WM_APP_LOG: {
        auto* text = reinterpret_cast<std::wstring*>(lparam);
        if (text) {
            handle_scan_log(*text);
            delete text;
        }
        return 0;
    }
    case WM_APP_DONE: {
        DWORD exitCode = static_cast<DWORD>(wparam);
        if (g_app.scanProcess.hThread) CloseHandle(g_app.scanProcess.hThread);
        if (g_app.scanProcess.hProcess) CloseHandle(g_app.scanProcess.hProcess);
        ZeroMemory(&g_app.scanProcess, sizeof(g_app.scanProcess));
        g_app.scanRunning.store(false);
        EnableWindow(g_app.start, TRUE);
        EnableWindow(g_app.stop, FALSE);
        EnableWindow(g_app.nextAngle, FALSE);
        std::wstringstream ss;
        ss << L"\r\n=== Scan finished with exit code " << exitCode << L" ===\r\n";
        append_log(g_app.log, ss.str());
        set_status(exitCode == 0 ? L"Finished" : L"Failed");
        return 0;
    }
    case WM_CLOSE:
        if (g_app.scanRunning.load()) {
            if (MessageBoxW(hwnd, L"A scan is running. Stop it and exit?", L"Exit", MB_YESNO | MB_ICONQUESTION) != IDYES) return 0;
            TerminateProcess(g_app.scanProcess.hProcess, 130);
        }
        DestroyWindow(hwnd);
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        break;
    }
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    g_app.instance = instance;
    g_app.root = get_exe_dir();

    INITCOMMONCONTROLSEX icc{};
    icc.dwSize = sizeof(icc);
    icc.dwICC = ICC_STANDARD_CLASSES | ICC_BAR_CLASSES;
    InitCommonControlsEx(&icc);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    WNDCLASSW wc{};
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = instance;
    wc.lpszClassName = kAppClass;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hIcon = LoadIcon(nullptr, IDI_APPLICATION);
    wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    RegisterClassW(&wc);

    HWND hwnd = CreateWindowExW(
        0, kAppClass, L"Structured Light Scan Controller",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 980, 790,
        nullptr, nullptr, instance, nullptr);

    if (!hwnd) return 1;
    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);

    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    CoUninitialize();
    return static_cast<int>(msg.wParam);
}
