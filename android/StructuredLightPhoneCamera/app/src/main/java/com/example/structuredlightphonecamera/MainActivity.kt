package com.example.structuredlightphonecamera

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.os.Bundle
import android.text.InputType
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.CompoundButton
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.OptIn
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit


data class CameraSettings(
    val manualExposure: Boolean = true,
    val exposureUs: Long = 10_000L,
    val iso: Int = 100,
    val manualFocus: Boolean = false,
    val focusDiopters: Float = 0.0f,
    val awbLocked: Boolean = false,
    val usePcSettings: Boolean = false,
)


data class ManualSupport(
    val cameraId: String = "",
    val manualSensor: Boolean = false,
    val aeOff: Boolean = false,
    val afAuto: Boolean = false,
    val afContinuous: Boolean = false,
    val focusDistance: Boolean = false,
    val awbOff: Boolean = false,
) {
    val canUseManual: Boolean
        get() = manualSensor && aeOff
}


class MainActivity : ComponentActivity() {
    private val tag = "SLPhoneCamera"
    private val preferencesName = "structured_light_phone_camera"
    private val wsUrlKey = "ws_url"
    private val defaultWsUrl = "ws://192.168.0.12:8765/ws"
    private val cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val httpClient = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .connectTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private lateinit var previewView: PreviewView
    private lateinit var wsUrlEdit: EditText
    private lateinit var exposureEdit: EditText
    private lateinit var isoEdit: EditText
    private lateinit var focusEdit: EditText
    private lateinit var manualSwitch: Switch
    private lateinit var manualFocusSwitch: Switch
    private lateinit var awbLockSwitch: Switch
    private lateinit var pcSettingsSwitch: Switch
    private lateinit var statusText: TextView
    private lateinit var recentText: TextView
    private lateinit var logText: TextView

    private var webSocket: WebSocket? = null
    private var camera: Camera? = null
    private var imageCapture: ImageCapture? = null
    private var settings = CameraSettings()
    private var boundSettings: CameraSettings? = null
    private var manualSupport = ManualSupport()

    private val cameraPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                appendLog("Camera permission granted")
                startCamera()
            } else {
                setStatus("error")
                appendLog("Camera permission denied")
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        buildUi()
        detectManualSupport()
        ensureCameraPermission()
    }

    override fun onDestroy() {
        webSocket?.close(1000, "Activity destroyed")
        cameraExecutor.shutdown()
        super.onDestroy()
    }

    private fun buildUi() {
        val savedWsUrl = getSharedPreferences(preferencesName, Context.MODE_PRIVATE)
            .getString(wsUrlKey, defaultWsUrl) ?: defaultWsUrl

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16, 16, 16, 16)
            setBackgroundColor(Color.rgb(245, 247, 250))
        }

        wsUrlEdit = EditText(this).apply {
            setText(savedWsUrl)
            hint = defaultWsUrl
            setSingleLine(true)
            inputType = InputType.TYPE_CLASS_TEXT or
                InputType.TYPE_TEXT_VARIATION_URI or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            imeOptions = EditorInfo.IME_ACTION_DONE
            isEnabled = true
            isFocusable = true
            isFocusableInTouchMode = true
            isCursorVisible = true
        }
        root.addView(label("PC WebSocket URL"))

        val urlRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        urlRow.addView(wsUrlEdit, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        urlRow.addView(Button(this).apply {
            text = "Paste"
            setOnClickListener { pasteWebSocketUrlFromClipboard() }
        })
        urlRow.addView(Button(this).apply {
            text = "Clear"
            setOnClickListener {
                wsUrlEdit.setText("")
                wsUrlEdit.requestFocus()
            }
        })
        root.addView(urlRow)

        val connectRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        connectRow.addView(Button(this).apply {
            text = "Connect"
            setOnClickListener { connectWebSocket() }
        })
        connectRow.addView(Button(this).apply {
            text = "Disconnect"
            setOnClickListener {
                webSocket?.close(1000, "User disconnected")
                webSocket = null
                setStatus("disconnected")
            }
        })
        statusText = TextView(this).apply {
            text = "disconnected"
            textSize = 16f
            setPadding(24, 0, 0, 0)
        }
        connectRow.addView(statusText)
        root.addView(connectRow)

        previewView = PreviewView(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1.0f,
            )
            scaleType = PreviewView.ScaleType.FIT_CENTER
            setOnTouchListener { _, event ->
                if (event.action == MotionEvent.ACTION_UP) {
                    focusAt(event.x, event.y, lock = true, label = "Tap focus lock")
                    true
                } else {
                    true
                }
            }
        }
        root.addView(previewView)

        manualSwitch = Switch(this).apply {
            text = "Manual exposure/ISO"
            isChecked = settings.manualExposure
            setOnCheckedChangeListener { _: CompoundButton, checked: Boolean ->
                settings = settings.copy(manualExposure = checked)
            }
        }
        root.addView(manualSwitch)

        pcSettingsSwitch = Switch(this).apply {
            text = "Use PC camera settings"
            isChecked = settings.usePcSettings
            setOnCheckedChangeListener { _: CompoundButton, checked: Boolean ->
                settings = settings.copy(usePcSettings = checked)
                appendLog(
                    if (checked) {
                        "PC capture settings will override phone camera settings"
                    } else {
                        "Phone camera settings will be kept during capture"
                    },
                )
            }
        }
        root.addView(pcSettingsSwitch)

        val cameraRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }
        exposureEdit = numericEdit(settings.exposureUs.toString(), "exposure us")
        isoEdit = numericEdit(settings.iso.toString(), "ISO")
        focusEdit = numericEdit(settings.focusDiopters.toString(), "focus diopters")
        cameraRow.addView(exposureEdit, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        cameraRow.addView(isoEdit, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        cameraRow.addView(focusEdit, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(cameraRow)

        manualFocusSwitch = Switch(this).apply {
            text = "Manual focus distance"
            isChecked = settings.manualFocus
            setOnCheckedChangeListener { _: CompoundButton, checked: Boolean ->
                settings = settings.copy(manualFocus = checked)
            }
        }
        root.addView(manualFocusSwitch)

        awbLockSwitch = Switch(this).apply {
            text = "Lock white balance if supported"
            isChecked = settings.awbLocked
            setOnCheckedChangeListener { _: CompoundButton, checked: Boolean ->
                settings = settings.copy(awbLocked = checked)
            }
        }
        root.addView(awbLockSwitch)

        val applyRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
        }
        applyRow.addView(Button(this).apply {
            text = "Apply"
            setOnClickListener {
                readUiSettings()
                bindUseCases()
            }
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        applyRow.addView(Button(this).apply {
            text = "AF Once"
            setOnClickListener { autofocusCenter() }
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        applyRow.addView(Button(this).apply {
            text = "Lock AF"
            setOnClickListener { lockFocusAtCenter() }
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        applyRow.addView(Button(this).apply {
            text = "Unlock AF"
            setOnClickListener { unlockAutofocus() }
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(applyRow)

        recentText = TextView(this).apply {
            text = "recent: none"
            textSize = 14f
            setPadding(0, 8, 0, 8)
        }
        root.addView(recentText)

        val scroll = ScrollView(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                220,
            )
        }
        logText = TextView(this).apply {
            textSize = 12f
            setTextColor(Color.rgb(20, 20, 20))
        }
        scroll.addView(logText)
        root.addView(scroll)

        setContentView(root)
    }

    private fun label(text: String): TextView =
        TextView(this).apply {
            this.text = text
            textSize = 12f
            setTextColor(Color.DKGRAY)
        }

    private fun numericEdit(value: String, hint: String): EditText =
        EditText(this).apply {
            setText(value)
            this.hint = hint
            setSingleLine(true)
            inputType = InputType.TYPE_CLASS_NUMBER or
                InputType.TYPE_NUMBER_FLAG_DECIMAL or
                InputType.TYPE_NUMBER_FLAG_SIGNED
        }

    private fun ensureCameraPermission() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        } else {
            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun readUiSettings() {
        settings = CameraSettings(
            manualExposure = manualSwitch.isChecked,
            exposureUs = exposureEdit.text.toString().toLongOrNull() ?: 10_000L,
            iso = isoEdit.text.toString().toIntOrNull() ?: 100,
            manualFocus = manualFocusSwitch.isChecked,
            focusDiopters = focusEdit.text.toString().toFloatOrNull() ?: 0.0f,
            awbLocked = awbLockSwitch.isChecked,
            usePcSettings = pcSettingsSwitch.isChecked,
        )
        appendLog("Requested camera settings: $settings")
    }

    private fun updateUiSettings() {
        runOnUiThread {
            manualSwitch.isChecked = settings.manualExposure
            manualFocusSwitch.isChecked = settings.manualFocus
            awbLockSwitch.isChecked = settings.awbLocked
            pcSettingsSwitch.isChecked = settings.usePcSettings
            exposureEdit.setText(settings.exposureUs.toString())
            isoEdit.setText(settings.iso.toString())
            focusEdit.setText(settings.focusDiopters.toString())
        }
    }

    private fun setStatus(status: String) {
        runOnUiThread {
            statusText.text = status
        }
        Log.i(tag, "status=$status")
    }

    private fun appendLog(message: String) {
        Log.i(tag, message)
        val stamp = SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date())
        runOnUiThread {
            logText.append("[$stamp] $message\n")
        }
    }

    private fun setRecent(message: String) {
        runOnUiThread {
            recentText.text = message
        }
    }

    private fun pasteWebSocketUrlFromClipboard() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val pasted = clipboard.primaryClip
            ?.takeIf { it.itemCount > 0 }
            ?.getItemAt(0)
            ?.coerceToText(this)
            ?.toString()
            ?.trim()

        if (pasted.isNullOrEmpty()) {
            appendLog("Clipboard is empty")
            return
        }

        wsUrlEdit.setText(normalizeWebSocketUrl(pasted))
        wsUrlEdit.setSelection(wsUrlEdit.text.length)
        appendLog("Pasted PC WebSocket URL")
    }

    private fun normalizeWebSocketUrl(rawValue: String): String {
        val value = rawValue.trim()
        if (value.isEmpty()) {
            return ""
        }
        if (value.startsWith("ws://") || value.startsWith("wss://")) {
            return value
        }
        if (value.startsWith("http://")) {
            return "ws://" + value.removePrefix("http://").removeSuffix("/upload").removeSuffix("/")
                .trimEnd('/') + "/ws"
        }
        if (value.startsWith("https://")) {
            return "wss://" + value.removePrefix("https://").removeSuffix("/upload").removeSuffix("/")
                .trimEnd('/') + "/ws"
        }
        if ("/" in value) {
            return "ws://$value"
        }
        val hostPort = if (":" in value) value else "$value:8765"
        return "ws://${hostPort}/ws"
    }

    private fun saveWebSocketUrl(url: String) {
        getSharedPreferences(preferencesName, Context.MODE_PRIVATE)
            .edit()
            .putString(wsUrlKey, url)
            .apply()
    }

    private fun detectManualSupport() {
        val manager = getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val backCameraId = manager.cameraIdList.firstOrNull { id ->
            manager.getCameraCharacteristics(id)
                .get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK
        } ?: return

        val chars = manager.getCameraCharacteristics(backCameraId)
        val capabilities = chars.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES)?.toSet().orEmpty()
        val aeModes = chars.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_MODES)?.toSet().orEmpty()
        val afModes = chars.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES)?.toSet().orEmpty()
        val awbModes = chars.get(CameraCharacteristics.CONTROL_AWB_AVAILABLE_MODES)?.toSet().orEmpty()
        val minFocus = chars.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE) ?: 0.0f

        manualSupport = ManualSupport(
            cameraId = backCameraId,
            manualSensor = capabilities.contains(
                CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR,
            ),
            aeOff = aeModes.contains(CaptureRequest.CONTROL_AE_MODE_OFF),
            afAuto = afModes.contains(CaptureRequest.CONTROL_AF_MODE_AUTO),
            afContinuous = afModes.contains(CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE),
            focusDistance = minFocus > 0.0f,
            awbOff = awbModes.contains(CaptureRequest.CONTROL_AWB_MODE_OFF),
        )
        appendLog("Back camera id=${manualSupport.cameraId}, manual support=$manualSupport")
    }

    private fun startCamera() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener(
            {
                bindUseCases()
            },
            ContextCompat.getMainExecutor(this),
        )
    }

    @OptIn(ExperimentalCamera2Interop::class)
    private fun bindUseCases() {
        val provider = ProcessCameraProvider.getInstance(this).get()
        provider.unbindAll()

        val previewBuilder = Preview.Builder()
        val imageCaptureBuilder = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
            .setFlashMode(ImageCapture.FLASH_MODE_OFF)
            .setJpegQuality(95)

        applyCamera2Interop(Camera2Interop.Extender(previewBuilder))
        applyCamera2Interop(Camera2Interop.Extender(imageCaptureBuilder))

        val preview = previewBuilder.build().also {
            it.setSurfaceProvider(previewView.surfaceProvider)
        }
        imageCapture = imageCaptureBuilder.build()

        camera = provider.bindToLifecycle(
            this,
            CameraSelector.DEFAULT_BACK_CAMERA,
            preview,
            imageCapture,
        )
        boundSettings = settings
        appendLog("Camera bound with settings: $settings")
    }

    private fun autofocusCenter() {
        focusAt(
            previewView.width / 2.0f,
            previewView.height / 2.0f,
            lock = false,
            label = "AF once center",
        )
    }

    private fun lockFocusAtCenter() {
        focusAt(
            previewView.width / 2.0f,
            previewView.height / 2.0f,
            lock = true,
            label = "Center focus lock",
        )
    }

    private fun focusAt(x: Float, y: Float, lock: Boolean, label: String) {
        val boundCamera = camera
        if (boundCamera == null) {
            appendLog("Focus lock skipped: camera is not bound")
            return
        }
        if (settings.manualFocus) {
            appendLog("AF skipped because Manual focus distance is enabled")
            return
        }
        val flags = if (settings.manualExposure) {
            FocusMeteringAction.FLAG_AF
        } else {
            FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE
        }
        val builder = FocusMeteringAction.Builder(
            previewView.meteringPointFactory.createPoint(x, y),
            flags,
        )
        if (lock) {
            builder.disableAutoCancel()
        } else {
            builder.setAutoCancelDuration(3, TimeUnit.SECONDS)
        }
        val future = boundCamera.cameraControl.startFocusAndMetering(builder.build())
        future.addListener(
            { appendLog("$label requested") },
            ContextCompat.getMainExecutor(this),
        )
    }

    private fun unlockAutofocus() {
        if (settings.manualFocus) {
            settings = settings.copy(manualFocus = false)
            updateUiSettings()
            bindUseCases()
        }
        camera?.cameraControl?.cancelFocusAndMetering()
        appendLog("AF unlocked; continuous autofocus can run if the camera supports it")
    }

    @OptIn(ExperimentalCamera2Interop::class)
    private fun applyCamera2Interop(extender: Camera2Interop.Extender<*>) {
        if (settings.manualExposure && manualSupport.canUseManual) {
            extender.setCaptureRequestOption(
                CaptureRequest.CONTROL_AE_MODE,
                CaptureRequest.CONTROL_AE_MODE_OFF,
            )
            extender.setCaptureRequestOption(
                CaptureRequest.SENSOR_EXPOSURE_TIME,
                settings.exposureUs * 1000L,
            )
            extender.setCaptureRequestOption(
                CaptureRequest.SENSOR_SENSITIVITY,
                settings.iso,
            )
        } else if (settings.manualExposure) {
            appendLog("Manual exposure/ISO unsupported; camera will use auto exposure")
        }

        if (settings.manualFocus && manualSupport.focusDistance) {
            extender.setCaptureRequestOption(
                CaptureRequest.CONTROL_AF_MODE,
                CaptureRequest.CONTROL_AF_MODE_OFF,
            )
            extender.setCaptureRequestOption(
                CaptureRequest.LENS_FOCUS_DISTANCE,
                settings.focusDiopters,
            )
        } else if (settings.manualFocus) {
            appendLog("Manual focus distance unsupported; camera will use autofocus")
        } else if (manualSupport.afContinuous) {
            extender.setCaptureRequestOption(
                CaptureRequest.CONTROL_AF_MODE,
                CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE,
            )
        } else if (manualSupport.afAuto) {
            extender.setCaptureRequestOption(
                CaptureRequest.CONTROL_AF_MODE,
                CaptureRequest.CONTROL_AF_MODE_AUTO,
            )
        }

        if (settings.awbLocked && manualSupport.awbOff) {
            extender.setCaptureRequestOption(
                CaptureRequest.CONTROL_AWB_MODE,
                CaptureRequest.CONTROL_AWB_MODE_OFF,
            )
        } else if (settings.awbLocked) {
            appendLog("White balance lock unsupported; camera will use auto white balance")
        }
    }

    private fun connectWebSocket() {
        val rawUrl = wsUrlEdit.text.toString().trim()
        if (rawUrl.isEmpty()) {
            appendLog("Enter PC WebSocket URL first")
            return
        }
        val url = normalizeWebSocketUrl(rawUrl)

        wsUrlEdit.setText(url)
        wsUrlEdit.setSelection(wsUrlEdit.text.length)
        saveWebSocketUrl(url)
        setStatus("connecting")
        val request = try {
            Request.Builder().url(url).build()
        } catch (exc: IllegalArgumentException) {
            setStatus("error")
            appendLog("Invalid PC WebSocket URL: ${exc.message}")
            return
        }
        webSocket = httpClient.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    setStatus("connected")
                    appendLog("WebSocket connected")
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    handleWebSocketMessage(text)
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    setStatus("error")
                    appendLog("WebSocket failure: ${t.message}")
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    setStatus("disconnected")
                    appendLog("WebSocket closed: $code $reason")
                }
            },
        )
    }

    private fun handleWebSocketMessage(text: String) {
        val message = JSONObject(text)
        when (message.optString("type")) {
            "ping" -> {
                val pong = JSONObject()
                    .put("type", "pong")
                    .put("timestamp_phone_ms", System.currentTimeMillis())
                webSocket?.send(pong.toString())
                appendLog("ping -> pong")
            }
            "capture" -> handleCaptureCommand(message)
            else -> appendLog("Unknown message: $text")
        }
    }

    private fun handleCaptureCommand(command: JSONObject) {
        val scanId = command.getString("scan_id")
        val patternId = command.getInt("pattern_id")
        val captureId = command.getInt("capture_id")
        val settingsJson = command.optJSONObject("settings")

        if (settingsJson != null && settings.usePcSettings) {
            val newSettings = settings.copy(
                manualExposure = settingsJson.optBoolean("manual", settings.manualExposure),
                exposureUs = settingsJson.optLong("exposure_us", settings.exposureUs),
                iso = settingsJson.optInt("iso", settings.iso),
                focusDiopters = settingsJson.optDouble("focus_diopters", settings.focusDiopters.toDouble()).toFloat(),
            )
            if (newSettings != settings) {
                settings = newSettings
                updateUiSettings()
                bindUseCases()
            }
        } else if (settingsJson != null && captureId == 0) {
            appendLog("Using phone camera settings; PC camera settings ignored")
        }

        val settleMs = settingsJson?.optLong("settle_ms_before_capture", 0L) ?: 0L
        setStatus("capturing")
        setRecent("recent command: scan=$scanId pattern=$patternId capture=$captureId")
        appendLog("Capture command scan=$scanId pattern=$patternId capture=$captureId settle=${settleMs}ms")

        previewView.postDelayed(
            {
                captureStill(command)
            },
            settleMs + if (boundSettings != settings) 250L else 0L,
        )
    }

    private fun captureStill(command: JSONObject) {
        // First version stores JPEG only. RAW/DNG should be added with a
        // dedicated Camera2 path after the synchronized JPEG loop is stable.
        val capture = imageCapture
        if (capture == null) {
            sendCaptureError(command, "ImageCapture is not ready")
            return
        }

        val scanId = command.getString("scan_id")
        val patternId = command.getInt("pattern_id")
        val captureId = command.getInt("capture_id")
        val angle = if (command.has("angle_deg")) command.optInt("angle_deg") else null
        val anglePart = angle?.let { "_angle_%03d".format(Locale.US, it) } ?: ""
        val filename = "%s%s_pattern_%03d_capture_%03d.jpg".format(
            Locale.US,
            scanId,
            anglePart,
            patternId,
            captureId,
        )

        val captureDir = File(cacheDir, "captures").apply { mkdirs() }
        val outputFile = File(captureDir, filename)
        val outputOptions = ImageCapture.OutputFileOptions.Builder(outputFile).build()

        capture.takePicture(
            outputOptions,
            cameraExecutor,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                    appendLog("Saved local image: ${outputFile.name}")
                    uploadCapture(command, outputFile)
                }

                override fun onError(exception: ImageCaptureException) {
                    sendCaptureError(command, "Camera capture failed: ${exception.message}")
                }
            },
        )
    }

    private fun uploadCapture(command: JSONObject, file: File) {
        setStatus("uploading")
        val scanId = command.getString("scan_id")
        val patternId = command.getInt("pattern_id")
        val captureId = command.getInt("capture_id")
        val uploadUrl = command.getString("upload_url")

        val bodyBuilder = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("scan_id", scanId)
            .addFormDataPart("pattern_id", patternId.toString())
            .addFormDataPart("capture_id", captureId.toString())
            .addFormDataPart(
                "file",
                file.name,
                file.asRequestBody("image/jpeg".toMediaType()),
            )
        if (command.has("angle_deg")) {
            bodyBuilder.addFormDataPart("angle_deg", command.optInt("angle_deg").toString())
        }

        val request = Request.Builder()
            .url(uploadUrl)
            .post(bodyBuilder.build())
            .build()

        httpClient.newCall(request).enqueue(
            object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    sendCaptureError(command, "Upload failed: ${e.message}")
                }

                override fun onResponse(call: Call, response: Response) {
                    response.use {
                        if (!response.isSuccessful) {
                            sendCaptureError(command, "Upload failed HTTP ${response.code}")
                            return
                        }
                    }
                    sendCaptureDone(command, file.name)
                }
            },
        )
    }

    private fun sendCaptureDone(command: JSONObject, filename: String) {
        val message = JSONObject()
            .put("type", "capture_done")
            .put("scan_id", command.getString("scan_id"))
            .put("pattern_id", command.getInt("pattern_id"))
            .put("capture_id", command.getInt("capture_id"))
            .put("filename", filename)
            .put("timestamp_phone_ms", System.currentTimeMillis())
            .put("upload_status", "ok")
        if (command.has("angle_deg")) {
            message.put("angle_deg", command.optInt("angle_deg"))
        }
        webSocket?.send(message.toString())
        setStatus("connected")
        setRecent("uploaded: $filename")
        appendLog("capture_done sent: $filename")
    }

    private fun sendCaptureError(command: JSONObject, error: String) {
        val message = JSONObject()
            .put("type", "capture_error")
            .put("scan_id", command.optString("scan_id", "unknown"))
            .put("pattern_id", command.optInt("pattern_id", -1))
            .put("capture_id", command.optInt("capture_id", -1))
            .put("error", error)
        if (command.has("angle_deg")) {
            message.put("angle_deg", command.optInt("angle_deg"))
        }
        webSocket?.send(message.toString())
        setStatus("error")
        appendLog(error)
    }
}
