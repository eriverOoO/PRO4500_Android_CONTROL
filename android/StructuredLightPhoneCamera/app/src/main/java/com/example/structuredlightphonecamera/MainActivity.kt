package com.example.structuredlightphonecamera

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
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
import androidx.camera.core.ImageProxy
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
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.zip.CRC32
import java.util.zip.Deflater
import java.util.zip.DeflaterOutputStream


private const val FIXED_EXPOSURE_US = 10_000L
private const val FIXED_ISO = 100
private const val FIXED_FOCUS_DIOPTERS = 0.0f


private data class RgbFrame(
    val pixels: ByteArray,
    val width: Int,
    val height: Int,
)


private class PngIdatOutputStream(
    private val output: DataOutputStream,
    bufferSize: Int,
) : OutputStream() {
    private val buffer = ByteArray(bufferSize)
    private var count = 0

    override fun write(value: Int) {
        if (count == buffer.size) {
            flushChunk()
        }
        buffer[count++] = value.toByte()
    }

    override fun write(source: ByteArray, offset: Int, length: Int) {
        var sourceOffset = offset
        var remaining = length
        while (remaining > 0) {
            if (count == buffer.size) {
                flushChunk()
            }
            val copyLength = minOf(remaining, buffer.size - count)
            source.copyInto(buffer, count, sourceOffset, sourceOffset + copyLength)
            count += copyLength
            sourceOffset += copyLength
            remaining -= copyLength
        }
    }

    override fun flush() {
        flushChunk()
        output.flush()
    }

    override fun close() {
        flushChunk()
    }

    private fun flushChunk() {
        if (count == 0) {
            return
        }
        val typeBytes = byteArrayOf('I'.code.toByte(), 'D'.code.toByte(), 'A'.code.toByte(), 'T'.code.toByte())
        val crc = CRC32().apply {
            update(typeBytes)
            update(buffer, 0, count)
        }
        output.writeInt(count)
        output.write(typeBytes)
        output.write(buffer, 0, count)
        output.writeInt(crc.value.toInt())
        count = 0
    }
}


data class CameraSettings(
    val manualExposure: Boolean = true,
    val exposureUs: Long = FIXED_EXPOSURE_US,
    val iso: Int = FIXED_ISO,
    val manualFocus: Boolean = false,
    val focusDiopters: Float = FIXED_FOCUS_DIOPTERS,
    val awbLocked: Boolean = true,
    val usePcSettings: Boolean = true,
)


data class ManualSupport(
    val cameraId: String = "",
    val manualSensor: Boolean = false,
    val aeOff: Boolean = false,
    val afAuto: Boolean = false,
    val afContinuous: Boolean = false,
    val focusDistance: Boolean = false,
    val minimumFocusDistance: Float = 0.0f,
    val awbOff: Boolean = false,
    val awbLock: Boolean = false,
    val edgeOff: Boolean = false,
    val noiseReductionOff: Boolean = false,
    val aberrationCorrectionOff: Boolean = false,
    val linearGamma: Boolean = false,
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
    private lateinit var autoFocusSwitch: Switch
    private lateinit var awbLockSwitch: Switch
    private lateinit var pcSettingsSwitch: Switch
    private lateinit var statusText: TextView
    private lateinit var recentText: TextView
    private lateinit var focusStatusText: TextView
    private lateinit var logText: TextView

    private var webSocket: WebSocket? = null
    private var camera: Camera? = null
    private var imageCapture: ImageCapture? = null
    private var settings = CameraSettings()
    private var boundSettings: CameraSettings? = null
    private var manualSupport = ManualSupport()
    private var autoFocusEnabled = true
    private var activeScanId: String? = null
    private var scanLockedFocusDiopters: Float? = null
    private var operatorLockedFocusDiopters: Float? = null
    private var scanFocusPrepared = false
    @Volatile private var lastLensFocusDiopters: Float? = null
    @Volatile private var lastAfState: Int? = null
    private var suppressFocusSwitchCallbacks = false

    private val captureResultCallback = object : CameraCaptureSession.CaptureCallback() {
        override fun onCaptureCompleted(
            session: CameraCaptureSession,
            request: CaptureRequest,
            result: TotalCaptureResult,
        ) {
            result.get(CaptureResult.LENS_FOCUS_DISTANCE)?.let { lastLensFocusDiopters = it }
            result.get(CaptureResult.CONTROL_AF_STATE)?.let { lastAfState = it }
        }
    }

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
                    autoFocusEnabled = false
                    setAutoFocusSwitchChecked(false)
                    lockFocusForOperator(event.x, event.y, "Tap focus lock")
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
            text = "Manual focus distance (overrides AF)"
            isChecked = settings.manualFocus
            setOnCheckedChangeListener { _: CompoundButton, checked: Boolean ->
                settings = settings.copy(manualFocus = checked)
                if (checked) {
                    autoFocusEnabled = false
                    operatorLockedFocusDiopters = null
                    setAutoFocusSwitchChecked(false)
                    updateFocusStatus("MANUAL requested: ${settings.focusDiopters} D (press Apply)")
                }
            }
        }
        root.addView(manualFocusSwitch)

        autoFocusSwitch = Switch(this).apply {
            text = "Auto focus (OFF = focus once and lock)"
            isChecked = autoFocusEnabled
            setOnCheckedChangeListener { _: CompoundButton, checked: Boolean ->
                if (!suppressFocusSwitchCallbacks) {
                    setAutoFocusEnabled(checked)
                }
            }
        }
        root.addView(autoFocusSwitch)

        focusStatusText = TextView(this).apply {
            text = "focus: AUTO (continuous preview; locks during scan)"
            textSize = 13f
            setTextColor(Color.rgb(30, 70, 120))
            setPadding(0, 2, 0, 6)
        }
        root.addView(focusStatusText)

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
            setOnClickListener {
                setAutoFocusSwitchChecked(false)
                autoFocusEnabled = false
                lockFocusAtCenter()
            }
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        applyRow.addView(Button(this).apply {
            text = "Lock AF"
            setOnClickListener {
                setAutoFocusSwitchChecked(false)
                autoFocusEnabled = false
                lockFocusAtCenter()
            }
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        applyRow.addView(Button(this).apply {
            text = "Resume AF"
            setOnClickListener {
                setAutoFocusSwitchChecked(true)
                setAutoFocusEnabled(true)
            }
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
            exposureUs = exposureEdit.text.toString().toLongOrNull() ?: FIXED_EXPOSURE_US,
            iso = isoEdit.text.toString().toIntOrNull() ?: FIXED_ISO,
            manualFocus = manualFocusSwitch.isChecked,
            focusDiopters = focusEdit.text.toString().toFloatOrNull() ?: FIXED_FOCUS_DIOPTERS,
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

    private fun setAutoFocusSwitchChecked(checked: Boolean) {
        if (!::autoFocusSwitch.isInitialized) return
        runOnUiThread {
            suppressFocusSwitchCallbacks = true
            autoFocusSwitch.isChecked = checked
            suppressFocusSwitchCallbacks = false
        }
    }

    private fun updateFocusStatus(status: String) {
        if (!::focusStatusText.isInitialized) return
        runOnUiThread {
            focusStatusText.text = "focus: $status"
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
        val awbLockAvailable = chars.get(CameraCharacteristics.CONTROL_AWB_LOCK_AVAILABLE) == true
        val edgeModes = chars.get(CameraCharacteristics.EDGE_AVAILABLE_EDGE_MODES)?.toSet().orEmpty()
        val noiseModes = chars.get(CameraCharacteristics.NOISE_REDUCTION_AVAILABLE_NOISE_REDUCTION_MODES)?.toSet().orEmpty()
        val aberrationModes = chars.get(CameraCharacteristics.COLOR_CORRECTION_AVAILABLE_ABERRATION_MODES)?.toSet().orEmpty()
        val tonemapModes = chars.get(CameraCharacteristics.TONEMAP_AVAILABLE_TONE_MAP_MODES)?.toSet().orEmpty()
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
            minimumFocusDistance = minFocus,
            awbOff = awbModes.contains(CaptureRequest.CONTROL_AWB_MODE_OFF),
            awbLock = awbLockAvailable,
            edgeOff = edgeModes.contains(CaptureRequest.EDGE_MODE_OFF),
            noiseReductionOff = noiseModes.contains(CaptureRequest.NOISE_REDUCTION_MODE_OFF),
            aberrationCorrectionOff = aberrationModes.contains(CaptureRequest.COLOR_CORRECTION_ABERRATION_MODE_OFF),
            linearGamma = tonemapModes.contains(CaptureRequest.TONEMAP_MODE_GAMMA_VALUE),
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
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
            .setFlashMode(ImageCapture.FLASH_MODE_OFF)
            .setBufferFormat(ImageFormat.YUV_420_888)

        Camera2Interop.Extender(previewBuilder).also {
            applyCamera2Interop(it)
            it.setSessionCaptureCallback(captureResultCallback)
        }
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
        when {
            scanLockedFocusDiopters != null -> updateFocusStatus(
                "LOCKED for scan: ${"%.3f".format(Locale.US, scanLockedFocusDiopters)} D",
            )
            operatorLockedFocusDiopters != null -> updateFocusStatus(
                "LOCKED by operator: ${"%.3f".format(Locale.US, operatorLockedFocusDiopters)} D",
            )
            settings.manualFocus -> updateFocusStatus(
                "MANUAL: ${"%.3f".format(Locale.US, settings.focusDiopters)} D",
            )
            autoFocusEnabled -> updateFocusStatus("AUTO (continuous preview; locks during scan)")
            else -> updateFocusStatus("AF lock requested")
        }
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
        lockFocusForOperator(
            previewView.width / 2.0f,
            previewView.height / 2.0f,
            "Center focus lock",
        )
    }

    private fun lockFocusForOperator(x: Float, y: Float, label: String) {
        focusAt(x, y, lock = true, label = label) {
            val measured = lastLensFocusDiopters
            if (manualSupport.focusDistance && measured != null) {
                operatorLockedFocusDiopters = measured.coerceIn(
                    0.0f,
                    manualSupport.minimumFocusDistance,
                )
                appendLog(
                    "Operator focus frozen at " +
                        "${"%.3f".format(Locale.US, operatorLockedFocusDiopters)} D",
                )
                bindUseCases()
            }
        }
    }

    private fun focusAt(x: Float, y: Float, lock: Boolean, label: String) {
        focusAt(x, y, lock, label, null)
    }

    private fun focusAt(
        x: Float,
        y: Float,
        lock: Boolean,
        label: String,
        onComplete: ((Boolean) -> Unit)?,
    ) {
        val boundCamera = camera
        if (boundCamera == null) {
            appendLog("Focus lock skipped: camera is not bound")
            onComplete?.invoke(false)
            return
        }
        if (settings.manualFocus) {
            appendLog("AF skipped because Manual focus distance is enabled")
            onComplete?.invoke(false)
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
            {
                val successful = try {
                    future.get().isFocusSuccessful
                } catch (exception: Exception) {
                    appendLog("$label failed: ${exception.message}")
                    false
                }
                val distance = lastLensFocusDiopters
                val afState = lastAfState
                appendLog(
                    "$label completed: success=$successful " +
                        "focus=${distance?.let { "%.3f D".format(Locale.US, it) } ?: "unknown"} " +
                        "afState=${afState ?: "unknown"}",
                )
                if (lock) {
                    updateFocusStatus(
                        "LOCKED: ${distance?.let { "%.3f D".format(Locale.US, it) } ?: "camera AF position"}",
                    )
                }
                onComplete?.invoke(successful)
            },
            ContextCompat.getMainExecutor(this),
        )
    }

    private fun setAutoFocusEnabled(enabled: Boolean) {
        autoFocusEnabled = enabled
        activeScanId = null
        scanLockedFocusDiopters = null
        if (!enabled) {
            if (settings.manualFocus) {
                updateFocusStatus("MANUAL: ${"%.3f".format(Locale.US, settings.focusDiopters)} D")
            } else {
                lockFocusAtCenter()
            }
            return
        }
        operatorLockedFocusDiopters = null
        if (settings.manualFocus) {
            settings = settings.copy(manualFocus = false)
            updateUiSettings()
            bindUseCases()
        }
        camera?.cameraControl?.cancelFocusAndMetering()
        updateFocusStatus("AUTO (continuous preview; locks during scan)")
        appendLog("AF resumed; continuous autofocus can run until capture starts")
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

        val lockedDistance = scanLockedFocusDiopters ?: operatorLockedFocusDiopters
        if (lockedDistance != null && manualSupport.focusDistance) {
            extender.setCaptureRequestOption(
                CaptureRequest.CONTROL_AF_MODE,
                CaptureRequest.CONTROL_AF_MODE_OFF,
            )
            extender.setCaptureRequestOption(
                CaptureRequest.LENS_FOCUS_DISTANCE,
                lockedDistance,
            )
        } else if (settings.manualFocus && manualSupport.focusDistance) {
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

        if (settings.awbLocked && manualSupport.awbLock) {
            extender.setCaptureRequestOption(
                CaptureRequest.CONTROL_AWB_LOCK,
                true,
            )
        } else if (settings.awbLocked && manualSupport.awbOff) {
            extender.setCaptureRequestOption(
                CaptureRequest.CONTROL_AWB_MODE,
                CaptureRequest.CONTROL_AWB_MODE_OFF,
            )
        } else if (settings.awbLocked) {
            appendLog("White balance lock unsupported; camera will use auto white balance")
        }

        if (manualSupport.edgeOff) {
            extender.setCaptureRequestOption(CaptureRequest.EDGE_MODE, CaptureRequest.EDGE_MODE_OFF)
        }
        if (manualSupport.noiseReductionOff) {
            extender.setCaptureRequestOption(
                CaptureRequest.NOISE_REDUCTION_MODE,
                CaptureRequest.NOISE_REDUCTION_MODE_OFF,
            )
        }
        if (manualSupport.aberrationCorrectionOff) {
            extender.setCaptureRequestOption(
                CaptureRequest.COLOR_CORRECTION_ABERRATION_MODE,
                CaptureRequest.COLOR_CORRECTION_ABERRATION_MODE_OFF,
            )
        }
        if (manualSupport.linearGamma) {
            extender.setCaptureRequestOption(
                CaptureRequest.TONEMAP_MODE,
                CaptureRequest.TONEMAP_MODE_GAMMA_VALUE,
            )
            extender.setCaptureRequestOption(CaptureRequest.TONEMAP_GAMMA, 1.0f)
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
        val progress = captureProgressText(command, patternId)

        if (activeScanId != scanId) {
            activeScanId = scanId
            scanLockedFocusDiopters = null
            scanFocusPrepared = false
            appendLog("New scan $scanId: autofocus will run once before the first capture")
        }

        if (settingsJson != null) {
            val newSettings = settings.copy(
                manualExposure = settingsJson.optBoolean("manual", settings.manualExposure),
                manualFocus = settingsJson.optBoolean("manual_focus", settings.manualFocus),
                awbLocked = settingsJson.optBoolean("awb_locked", settings.awbLocked),
                exposureUs = settingsJson.optLong("exposure_us", settings.exposureUs),
                iso = settingsJson.optInt("iso", settings.iso),
                focusDiopters = settingsJson.optDouble("focus_diopters", settings.focusDiopters.toDouble()).toFloat(),
                usePcSettings = true,
            )
            if (newSettings != settings) {
                settings = newSettings
                updateUiSettings()
                bindUseCases()
            }
        }

        val settleMs = settingsJson?.optLong("settle_ms_before_capture", 0L) ?: 0L
        val bracketLabel = command.optString("bracket_label", "")
        setStatus("capturing")
        setRecent("recent command: scan=$scanId $progress bracket=$bracketLabel capture=$captureId")
        appendLog("Capture command scan=$scanId $progress bracket=$bracketLabel capture=$captureId settle=${settleMs}ms")

        previewView.postDelayed(
            {
                prepareFocusForCapture(command)
            },
            settleMs + if (boundSettings != settings) 250L else 0L,
        )
    }

    private fun prepareFocusForCapture(command: JSONObject) {
        if (settings.manualFocus || !autoFocusEnabled || scanFocusPrepared) {
            captureStill(command)
            return
        }

        updateFocusStatus("AUTOFOCUSING before scan capture...")
        appendLog("Running one-shot center AF before scan; focus will remain fixed for all frames")
        focusAt(
            previewView.width / 2.0f,
            previewView.height / 2.0f,
            lock = true,
            label = "Pre-scan AF lock",
            onComplete = {
                scanFocusPrepared = true
                val measured = lastLensFocusDiopters
                if (manualSupport.focusDistance && measured != null) {
                    scanLockedFocusDiopters = measured.coerceIn(
                        0.0f,
                        manualSupport.minimumFocusDistance,
                    )
                    appendLog(
                        "Scan focus frozen at " +
                            "${"%.3f".format(Locale.US, scanLockedFocusDiopters)} D",
                    )
                    bindUseCases()
                    previewView.postDelayed({ captureStill(command) }, 300L)
                } else {
                    appendLog("Lens distance unavailable; holding CameraX AF metering lock for this scan")
                    captureStill(command)
                }
            },
        )
    }

    private fun captureStill(command: JSONObject) {
        val capture = imageCapture
        if (capture == null) {
            sendCaptureError(command, "ImageCapture is not ready")
            return
        }

        val scanId = command.getString("scan_id")
        val patternId = command.getInt("pattern_id")
        val captureId = command.getInt("capture_id")
        val angle = if (command.has("angle_deg")) command.optInt("angle_deg") else null
        val angleDir = angle?.let { "angle_%03d".format(Locale.US, it) } ?: "angle_unknown"
        val bracketLabel = safeFilenameStem(command.optString("bracket_label", "frame"))
        val patternLabel = safeFilenameStem(command.optString("pattern_label", "pattern"))
        val filename = "pattern_%03d_%s_%s_capture_%03d.png".format(
            Locale.US,
            patternId,
            patternLabel,
            bracketLabel,
            captureId,
        )

        val captureDir = File(
            File(File(cacheDir, "captures"), scanId),
            "$angleDir/pattern_%03d".format(Locale.US, patternId),
        ).apply { mkdirs() }
        val outputFile = File(captureDir, filename)
        val captureStartedNs = System.nanoTime()

        capture.takePicture(
            cameraExecutor,
            object : ImageCapture.OnImageCapturedCallback() {
                override fun onCaptureSuccess(image: ImageProxy) {
                    try {
                        val captureMs = elapsedMs(captureStartedNs)
                        val encodeStartedNs = System.nanoTime()
                        val frame = try {
                            imageProxyToRgb(image)
                        } finally {
                            image.close()
                        }
                        writeRgbPng(outputFile, frame)
                        val encodeMs = elapsedMs(encodeStartedNs)
                        appendLog(
                            "Saved lossless RGB PNG from YUV_420_888: ${outputFile.name} " +
                                "${frame.width}x${frame.height}, ${outputFile.length() / 1024} KiB, " +
                                "capture=${captureMs}ms encode=${encodeMs}ms",
                        )
                        uploadCapture(command, outputFile)
                    } catch (exception: Exception) {
                        sendCaptureError(command, "Lossless RGB PNG save failed: ${exception.message}")
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    sendCaptureError(command, "Camera capture failed: ${exception.message}")
                }
            },
        )
    }

    private fun imageProxyToRgb(image: ImageProxy): RgbFrame {
        if (image.format != ImageFormat.YUV_420_888 || image.planes.size < 3) {
            throw IOException("Expected YUV capture, received format ${image.format}")
        }

        val crop = image.cropRect
        val width = crop.width()
        val height = crop.height()
        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]
        val yBuffer = yPlane.buffer.duplicate()
        val uBuffer = uPlane.buffer.duplicate()
        val vBuffer = vPlane.buffer.duplicate()
        val rotationDegrees = image.imageInfo.rotationDegrees
        val rotation = ((rotationDegrees % 360) + 360) % 360
        if (rotation !in setOf(90, 180, 270)) {
            if (rotation != 0) {
                throw IOException("Unsupported capture rotation: $rotationDegrees")
            }
        }

        val destinationWidth = if (rotation == 0 || rotation == 180) width else height
        val destinationHeight = if (rotation == 0 || rotation == 180) height else width
        val destination = ByteArray(width * height * 3)

        for (sourceY in 0 until height) {
            for (sourceX in 0 until width) {
                val destinationX: Int
                val destinationY: Int
                when (rotation) {
                    0 -> {
                        destinationX = sourceX
                        destinationY = sourceY
                    }
                    90 -> {
                        destinationX = height - 1 - sourceY
                        destinationY = sourceX
                    }
                    180 -> {
                        destinationX = width - 1 - sourceX
                        destinationY = height - 1 - sourceY
                    }
                    else -> {
                        destinationX = sourceY
                        destinationY = width - 1 - sourceX
                    }
                }
                val absoluteX = crop.left + sourceX
                val absoluteY = crop.top + sourceY
                val y = yBuffer.get(
                    absoluteY * yPlane.rowStride + absoluteX * yPlane.pixelStride,
                ).toInt() and 0xff
                val chromaX = absoluteX / 2
                val chromaY = absoluteY / 2
                val u = (uBuffer.get(
                    chromaY * uPlane.rowStride + chromaX * uPlane.pixelStride,
                ).toInt() and 0xff) - 128
                val v = (vBuffer.get(
                    chromaY * vPlane.rowStride + chromaX * vPlane.pixelStride,
                ).toInt() and 0xff) - 128
                val c = maxOf(0, y - 16)
                val r = ((298 * c + 409 * v + 128) shr 8).coerceIn(0, 255)
                val g = ((298 * c - 100 * u - 208 * v + 128) shr 8).coerceIn(0, 255)
                val b = ((298 * c + 516 * u + 128) shr 8).coerceIn(0, 255)
                val destinationOffset = (destinationY * destinationWidth + destinationX) * 3
                destination[destinationOffset] = r.toByte()
                destination[destinationOffset + 1] = g.toByte()
                destination[destinationOffset + 2] = b.toByte()
            }
        }
        return RgbFrame(destination, destinationWidth, destinationHeight)
    }

    private fun writeRgbPng(file: File, frame: RgbFrame) {
        DataOutputStream(BufferedOutputStream(file.outputStream(), 64 * 1024)).use { output ->
            output.write(byteArrayOf(137.toByte(), 80, 78, 71, 13, 10, 26, 10))

            val headerBytes = ByteArrayOutputStream(13)
            DataOutputStream(headerBytes).use { header ->
                header.writeInt(frame.width)
                header.writeInt(frame.height)
                header.writeByte(8)
                header.writeByte(2) // RGB, three bytes per pixel.
                header.writeByte(0)
                header.writeByte(0)
                header.writeByte(0)
            }
            writePngChunk(output, "IHDR", headerBytes.toByteArray())

            // Level 3 reduces Wi-Fi payload substantially while keeping capture latency low.
            // DEFLATE and PNG row filtering are fully lossless at every compression level.
            val deflater = Deflater(3)
            try {
                DeflaterOutputStream(
                    PngIdatOutputStream(output, 64 * 1024),
                    deflater,
                    64 * 1024,
                ).use { stream ->
                    val rowBytes = frame.width * 3
                    val filteredRow = ByteArray(rowBytes + 1)
                    filteredRow[0] = 1 // PNG Sub filter; effective for projected stripe patterns.
                    for (row in 0 until frame.height) {
                        val rowOffset = row * rowBytes
                        for (column in 0 until rowBytes) {
                            val value = frame.pixels[rowOffset + column].toInt() and 0xff
                            val left = if (column >= 3) {
                                frame.pixels[rowOffset + column - 3].toInt() and 0xff
                            } else {
                                0
                            }
                            filteredRow[column + 1] = (value - left).toByte()
                        }
                        stream.write(filteredRow)
                    }
                }
            } finally {
                deflater.end()
            }
            writePngChunk(output, "IEND", ByteArray(0))
        }
    }

    private fun writePngChunk(output: DataOutputStream, type: String, data: ByteArray) {
        val typeBytes = type.toByteArray(Charsets.US_ASCII)
        val crc = CRC32().apply {
            update(typeBytes)
            update(data)
        }
        output.writeInt(data.size)
        output.write(typeBytes)
        output.write(data)
        output.writeInt(crc.value.toInt())
    }

    private fun elapsedMs(startedNs: Long): Long {
        return TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedNs)
    }

    private fun uploadCapture(command: JSONObject, file: File) {
        setStatus("uploading")
        val uploadStartedNs = System.nanoTime()
        val scanId = command.getString("scan_id")
        val patternId = command.getInt("pattern_id")
        val captureId = command.getInt("capture_id")
        val uploadUrl = command.getString("upload_url")
        val settingsJson = command.optJSONObject("settings")

        val bodyBuilder = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("scan_id", scanId)
            .addFormDataPart("pattern_id", patternId.toString())
            .addFormDataPart("capture_id", captureId.toString())
            .addFormDataPart(
                "file",
                file.name,
                file.asRequestBody("image/png".toMediaType()),
            )
            .addFormDataPart("source_format", "YUV_420_888")
            .addFormDataPart("encoded_format", "rgb_png")
            .addFormDataPart("source_bit_depth", "8")
            .addFormDataPart("compression", "png_deflate_level_3_lossless")
        if (command.has("angle_deg")) {
            bodyBuilder.addFormDataPart("angle_deg", command.optInt("angle_deg").toString())
        }
        addOptionalIntPart(bodyBuilder, command, "pattern_sequence_index")
        addOptionalIntPart(bodyBuilder, command, "pattern_count")
        addOptionalIntPart(bodyBuilder, command, "angle_index")
        addOptionalIntPart(bodyBuilder, command, "angle_count")
        if (command.has("bracket_label")) {
            bodyBuilder.addFormDataPart("bracket_label", command.optString("bracket_label"))
        }
        if (settingsJson != null) {
            bodyBuilder
                .addFormDataPart("exposure_us", settingsJson.optLong("exposure_us", settings.exposureUs).toString())
                .addFormDataPart("iso", settingsJson.optInt("iso", settings.iso).toString())
                .addFormDataPart(
                    "focus_diopters",
                    ((scanLockedFocusDiopters ?: operatorLockedFocusDiopters)?.toDouble()
                        ?: settingsJson.optDouble("focus_diopters", settings.focusDiopters.toDouble())).toString(),
                )
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
                    val filename = file.name
                    appendLog(
                        "Upload complete: $filename ${file.length() / 1024} KiB " +
                            "in ${elapsedMs(uploadStartedNs)}ms",
                    )
                    if (!file.delete()) {
                        appendLog("Could not remove uploaded cache file: $filename")
                    }
                    sendCaptureDone(command, filename)
                    finishScanFocusIfNeeded(command)
                }
            },
        )
    }

    private fun finishScanFocusIfNeeded(command: JSONObject) {
        val bracket = command.optJSONObject("bracket")
        val bracketIndex = bracket?.optInt("index", 0) ?: 0
        val bracketCount = bracket?.optInt("count", command.optInt("bracket_count", 1)) ?: 1
        val lastPattern = command.optInt("pattern_sequence_index", 0) ==
            command.optInt("pattern_count", 1) - 1
        val lastAngle = command.optInt("angle_index", 0) ==
            command.optInt("angle_count", 1) - 1
        val lastBracket = bracketIndex == bracketCount - 1
        if (!lastPattern || !lastAngle || !lastBracket) return

        appendLog("Scan capture complete; releasing fixed focus and resuming preview AF")
        activeScanId = null
        scanFocusPrepared = false
        scanLockedFocusDiopters = null
        if (autoFocusEnabled && !settings.manualFocus) {
            runOnUiThread {
                bindUseCases()
                camera?.cameraControl?.cancelFocusAndMetering()
            }
            updateFocusStatus("AUTO (continuous preview; locks during scan)")
        }
    }

    private fun sendCaptureDone(command: JSONObject, filename: String) {
        val settingsJson = command.optJSONObject("settings")
        val message = JSONObject()
            .put("type", "capture_done")
            .put("scan_id", command.getString("scan_id"))
            .put("pattern_id", command.getInt("pattern_id"))
            .put("capture_id", command.getInt("capture_id"))
            .put("filename", filename)
            .put("timestamp_phone_ms", System.currentTimeMillis())
            .put("upload_status", "ok")
            .put("pattern_label", command.optString("pattern_label", ""))
            .put("bracket_label", command.optString("bracket_label", ""))
        copyOptionalInt(command, message, "pattern_sequence_index")
        copyOptionalInt(command, message, "pattern_count")
        copyOptionalInt(command, message, "angle_index")
        copyOptionalInt(command, message, "angle_count")
        if (command.has("angle_deg")) {
            message.put("angle_deg", command.optInt("angle_deg"))
        }
        if (settingsJson != null) {
            message.put(
                "settings",
                JSONObject()
                    .put("manual", settingsJson.optBoolean("manual", settings.manualExposure))
                    .put("manual_focus", settingsJson.optBoolean("manual_focus", settings.manualFocus))
                    .put("awb_locked", settingsJson.optBoolean("awb_locked", settings.awbLocked))
                    .put("exposure_us", settingsJson.optLong("exposure_us", settings.exposureUs))
                    .put("iso", settingsJson.optInt("iso", settings.iso))
                    .put(
                        "focus_diopters",
                        (scanLockedFocusDiopters ?: operatorLockedFocusDiopters)?.toDouble()
                            ?: settingsJson.optDouble("focus_diopters", settings.focusDiopters.toDouble()),
                    ),
            )
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
            .put("pattern_label", command.optString("pattern_label", ""))
            .put("bracket_label", command.optString("bracket_label", ""))
            .put("error", error)
        copyOptionalInt(command, message, "pattern_sequence_index")
        copyOptionalInt(command, message, "pattern_count")
        copyOptionalInt(command, message, "angle_index")
        copyOptionalInt(command, message, "angle_count")
        if (command.has("angle_deg")) {
            message.put("angle_deg", command.optInt("angle_deg"))
        }
        webSocket?.send(message.toString())
        setStatus("error")
        appendLog(error)
    }

    private fun captureProgressText(command: JSONObject, patternId: Int): String {
        val patternIndex = command.optInt("pattern_sequence_index", patternId)
        val patternCount = command.optInt("pattern_count", 0)
        val angle = if (command.has("angle_deg")) command.optInt("angle_deg").toString() else "?"
        val angleIndex = command.optInt("angle_index", 0)
        val angleCount = command.optInt("angle_count", 0)
        val patternText = if (patternCount > 0) {
            "pattern=${patternIndex + 1}/$patternCount(id=$patternId)"
        } else {
            "pattern=$patternId"
        }
        val angleText = if (angleCount > 0) {
            "angle=${angleIndex + 1}/$angleCount(${angle}deg)"
        } else {
            "angle=${angle}deg"
        }
        return "$angleText $patternText"
    }

    private fun safeFilenameStem(value: String): String {
        val sanitized = value.trim().replace(Regex("[^A-Za-z0-9_.-]+"), "_").trim('.', '_')
        return sanitized.ifEmpty { "frame" }
    }

    private fun addOptionalIntPart(builder: MultipartBody.Builder, source: JSONObject, key: String) {
        if (source.has(key)) {
            builder.addFormDataPart(key, source.optInt(key).toString())
        }
    }

    private fun copyOptionalInt(source: JSONObject, destination: JSONObject, key: String) {
        if (source.has(key)) {
            destination.put(key, source.optInt(key))
        }
    }
}
