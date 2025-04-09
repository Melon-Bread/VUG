"""
Video Upscaler GUI
"""
import os
import sys
import subprocess
import tempfile
import shutil
import re
import threading
import time
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QFileDialog,
    QTextEdit,
    QSpinBox,
    QProgressBar,
)
from PySide6.QtCore import QTimer, Qt, Signal, QObject
from PySide6.QtGui import QIcon


# Supported video file extensions
SUPPORTED_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".mpeg", ".mpg")


class UpscaleWorker(QObject):
    log_signal = Signal(str)  # Signal to send log messages
    finished_signal = Signal()  # Signal to indicate process completion
    progress_signal = Signal(int, int)  # Signal for frame progress (current, total)

    def __init__(self, input_path, output_path, scale, model, batch_size, bulk_mode=False):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.scale = scale
        self.model = model
        self.batch_size = batch_size
        self.bulk_mode = bulk_mode

    def run(self):
        """Run the upscaling process in a separate thread"""
        try:
            if self.bulk_mode:
                if not os.path.isdir(self.input_path):
                    raise ValueError("Bulk mode requires input to be a directory")
                # Create output directory if it doesn't exist
                Path(self.output_path).mkdir(parents=True, exist_ok=True)
                self.upscale_directory(self.input_path, self.output_path)
            else:
                if not os.path.isfile(self.input_path):
                    raise ValueError("Single mode requires input to be a file")
                output_file_path = self.output_path
                output_dir = os.path.dirname(output_file_path)
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                self.upscale_single_video(self.input_path, output_file_path)
        except Exception as e:
            self.log_signal.emit(f"Error: {str(e)}")
        finally:
            self.finished_signal.emit()

    def upscale_single_video(self, input_video, output_file):
        """Upscale a single video file"""
        # Create temp directories
        temp_dir = tempfile.mkdtemp()
        original_dir = os.path.join(temp_dir, "original")
        upscaled_dir = os.path.join(temp_dir, "upscaled")
        Path(original_dir).mkdir()
        Path(upscaled_dir).mkdir()

        # Get FPS
        fps = self.get_video_fps(input_video)
        if not fps:
            raise ValueError("Could not detect FPS")

        # Extract frames
        self.log_signal.emit(f"Extracting frames from {input_video}...")
        self.extract_frames(input_video, original_dir)

        # Upscale frames
        self.log_signal.emit(f"Upscaling frames from {input_video}...")
        self.upscale_frames(original_dir, upscaled_dir, self.scale, self.model, self.batch_size)

        # Combine frames
        self.log_signal.emit(f"Combining results for {input_video}...")
        self.combine_frames(upscaled_dir, output_file, fps, input_video)

        self.log_signal.emit(f"Done! Saved to: {output_file}")

        # Clean up
        shutil.rmtree(temp_dir)

    def upscale_directory(self, input_dir, output_dir):
        """Upscale all video files in a directory"""
        video_files = self.find_video_files(input_dir)
        if not video_files:
            raise ValueError("No supported video files found in the directory")

        for video_file in video_files:
            relative_path = os.path.relpath(video_file, input_dir)
            output_subdir = os.path.join(output_dir, os.path.dirname(relative_path))
            self.upscale_single_video(video_file, os.path.join(
                output_subdir,
                f"upscaled_{Path(video_file).stem}.mp4"
            ))

    def find_video_files(self, directory):
        """Recursively find all supported video files in a directory"""
        video_files = []
        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith(SUPPORTED_EXTENSIONS):
                    video_files.append(os.path.join(root, file))
        return video_files

    def get_video_fps(self, input_video):
        """Accurately extract FPS using regex"""
        cmd = ["ffmpeg", "-i", input_video]
        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        output = result.stderr
        fps_match = re.search(r"(\d+(?:\.\d+)?)\s+fps", output)
        return float(fps_match.group(1)) if fps_match else None

    def extract_frames(self, input_video, output_dir):
        """Extract frames to directory and log output"""
        frame_pattern = os.path.join(output_dir, "frame_%04d.png")
        process = subprocess.Popen(
            ["ffmpeg", "-i", input_video, "-qscale:v", "1", frame_pattern],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        
        for line in process.stderr:
            self.log_signal.emit(line.strip())
        
        # After extraction completes, update progress to 20%
        self.progress_signal.emit(20, 100)

    def upscale_frames(self, input_dir, output_dir, scale, model, batch_size):
        """Upscale frames with polling-based progress update"""
        # Count input frames before starting
        input_frames = len([f for f in os.listdir(input_dir) if f.endswith('.png')])
        if input_frames == 0:
            input_frames = 1  # avoid division by zero

        self.current_progress = 20
        self.target_progress = 20

        # Start realesrgan subprocess
        process = subprocess.Popen(
            [
                "realesrgan-ncnn-vulkan",
                "-i", input_dir,
                "-o", output_dir,
                "-s", str(scale),
                "-n", model,
                "-j", f"{batch_size}:{batch_size}:{batch_size}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            while process.poll() is None:
                time.sleep(0.2)
                try:
                    output_frames = len([f for f in os.listdir(output_dir) if f.endswith('.png')])
                    ratio = output_frames / input_frames
                    if ratio > 1:
                        ratio = 1
                    self.target_progress = 20 + ratio * 70
                    if self.target_progress > 90:
                        self.target_progress = 90
                except:
                    pass

                # Smooth interpolation
                self.current_progress += (self.target_progress - self.current_progress) * 0.2
                self.progress_signal.emit(int(self.current_progress), 100)

                # Process log output without blocking
                if process.stderr:
                    try:
                        line = process.stderr.readline()
                        if line:
                            self.log_signal.emit(line.strip())
                    except:
                        pass
        finally:
            pass

        # Final update to 90% after process ends
        self.current_progress = max(self.current_progress, 90)
        self.progress_signal.emit(int(self.current_progress), 100)

        # Drain any remaining stderr
        for line in process.stderr:
            self.log_signal.emit(line.strip())

    def combine_frames(self, frames_dir, output_file, fps, input_video):
        """Combine frames into video and log output"""
        # Set initial progress to 90%
        self.progress_signal.emit(90, 100)
        
        frame_pattern = os.path.join(frames_dir, "frame_%04d.png")
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-framerate", str(fps),
                "-i", frame_pattern,
                "-i", input_video,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "libx264",
                "-crf", "18",
                "-c:a", "copy",
                output_file,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        
        # Monitor process completion for progress updates
        while process.poll() is None:
            time.sleep(0.5)
            # Estimate progress based on output file growth
            try:
                if os.path.exists(output_file):
                    size = os.path.getsize(output_file)
                    # Simple heuristic - assume final size ~100MB
                    progress = 90 + min(10, size / (100 * 1024 * 1024) * 10)
                    self.progress_signal.emit(int(progress), 100)
            except:
                pass
            
            # Process log output
            for line in process.stderr:
                self.log_signal.emit(line.strip())


class VUG(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VUG")

        # Set window icon
        icon_path = Path(__file__).parent.parent.parent / "icons" / "VUG.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            # Add a warning if the icon isn't found, useful for debugging
            print(f"Warning: Icon file not found at expected path: {icon_path}", file=sys.stderr)
        self.setGeometry(100, 100, 600, 500)
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.layout = QVBoxLayout()
        self.main_widget.setLayout(self.layout)

        # Input Video/Directory
        self.input_label = QLabel("Input Video File:")
        self.input_input = QLineEdit()
        self.input_input.setReadOnly(False)
        self.input_button = QPushButton("Browse...")
        self.input_button.clicked.connect(self.select_input)
        input_box = QHBoxLayout()
        input_box.addWidget(self.input_label)
        input_box.addWidget(self.input_input)
        input_box.addWidget(self.input_button)
        self.layout.addLayout(input_box)

        # Output Directory
        self.output_dir_label = QLabel("Output Video File::")
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setReadOnly(True)
        self.output_dir_button = QPushButton("Browse...")
        self.output_dir_button.clicked.connect(self.select_output_dir)
        output_dir_box = QHBoxLayout()
        output_dir_box.addWidget(self.output_dir_label)
        output_dir_box.addWidget(self.output_dir_input)
        output_dir_box.addWidget(self.output_dir_button)
        self.layout.addLayout(output_dir_box)
        
        # Bulk Mode Checkbox
        from PySide6.QtWidgets import QCheckBox
        self.bulk_checkbox = QCheckBox("Bulk Mode")
        self.bulk_checkbox.setChecked(False)
        self.bulk_checkbox.stateChanged.connect(self.toggle_bulk_mode)
        self.layout.addWidget(self.bulk_checkbox)
        
        # Scale Factor
        self.scale_factor_label = QLabel("Scale Factor:")
        self.scale_factor_input = QComboBox()
        self.scale_factor_input.addItems(["2", "3", "4"])
        scale_factor_box = QHBoxLayout()
        scale_factor_box.addWidget(self.scale_factor_label)
        scale_factor_box.addWidget(self.scale_factor_input)
        self.layout.addLayout(scale_factor_box)

        # Model Selection
        self.model_label = QLabel("Model:")
        self.model_input = QComboBox()
        self.model_input.addItems([
            "realesr-animevideov3",
            "realesrgan-x4plus",
            "realesrgan-x4plus-anime",
            "realesrnet-x4plus",
        ])
        model_box = QHBoxLayout()
        model_box.addWidget(self.model_label)
        model_box.addWidget(self.model_input)
        self.layout.addLayout(model_box)

        # Batch Size
        self.batch_size_label = QLabel("Batch Size:")
        self.batch_size_input = QSpinBox()
        self.batch_size_input.setRange(1, 16)  # Allow batch sizes from 1 to 16
        self.batch_size_input.setValue(2)  # Default batch size
        batch_size_box = QHBoxLayout()
        batch_size_box.addWidget(self.batch_size_label)
        batch_size_box.addWidget(self.batch_size_input)
        self.layout.addLayout(batch_size_box)

        # Log Box
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("Log output will appear here...")
        self.layout.addWidget(self.log_box)

        # Upscale Button
        self.upscale_button = QPushButton("Upscale Video(s)")
        self.upscale_button.clicked.connect(self.upscale_video)
        self.layout.addWidget(self.upscale_button)

        # Progress widgets
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFormat("%p%")  # Show percentage
        
        self.task_duration_label = QLabel("Task Duration: 00:00:00")
        self.task_duration_label.setAlignment(Qt.AlignCenter)
        
        progress_box = QHBoxLayout()
        progress_box.addWidget(self.progress_bar, stretch=2)
        progress_box.addWidget(self.task_duration_label, stretch=1)
        self.layout.addLayout(progress_box)

        # Task timing
        self.task_start_time = None
        self.task_timer = QTimer()
        self.task_timer.timeout.connect(self.update_task_duration)

        # Worker for upscaling process
        self.worker = None
        self.worker_thread = None

    def select_input(self):
        """Open file/directory dialog to select input"""
        if self.bulk_checkbox.isChecked():
            # Bulk mode: select directory
            dir_dialog = QFileDialog()
            dir_dialog.setFileMode(QFileDialog.Directory)
            if dir_dialog.exec():
                selected_dirs = dir_dialog.selectedFiles()
                if selected_dirs:
                    self.input_input.setText(selected_dirs[0])
                    # Set default output dir to input dir
                    self.output_dir_input.setText(selected_dirs[0])
        else:
            # Single mode: select file
            file_dialog = QFileDialog()
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter(
                "Videos (*.mp4 *.mov *.avi *.mkv *.flv *.wmv *.webm *.mpeg *.mpg);;"
                "All Files (*)"
            )
            if file_dialog.exec():
                selected_files = file_dialog.selectedFiles()
                if selected_files:
                    self.input_input.setText(selected_files[0])
                    # Set default output file path next to input, enforce .mp4 extension
                    input_path = Path(selected_files[0])
                    default_out = input_path.parent / f"upscaled_{input_path.stem}.mp4"
                    self.output_dir_input.setText(str(default_out))

    def select_output_dir(self):
        """Open dialog to select output location"""
        if self.bulk_checkbox.isChecked():
            # Bulk mode: select directory
            dir_dialog = QFileDialog()
            dir_dialog.setFileMode(QFileDialog.Directory)
            if dir_dialog.exec():
                selected_dirs = dir_dialog.selectedFiles()
                if selected_dirs:
                    self.output_dir_input.setText(selected_dirs[0])
        else:
            # Single mode: select output file
            file_dialog = QFileDialog()
            file_dialog.setAcceptMode(QFileDialog.AcceptSave)
            file_dialog.setNameFilter(
                "Videos (*.mp4 *.mov *.avi *.mkv *.flv *.wmv *.webm *.mpeg *.mpg);;"
                "All Files (*)"
            )
            if file_dialog.exec():
                selected_files = file_dialog.selectedFiles()
                if selected_files:
                    self.output_dir_input.setText(selected_files[0])

    def log_message(self, message):
        """Append a message to the log box"""
        self.log_box.append(message)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def toggle_bulk_mode(self):
        """Toggle between single and bulk mode"""
        if self.bulk_checkbox.isChecked():
            self.input_label.setText("Input Directory:")
            self.output_dir_label.setText("Output Directory:")
            # Clear previous paths
            self.input_input.clear()
            self.output_dir_input.clear()
        else:
            self.input_label.setText("Input Video File:")
            self.output_dir_label.setText("Output Video File:")
            # Clear previous paths
            self.input_input.clear()
            self.output_dir_input.clear()

    def upscale_video(self):
        """Start the upscaling process in a separate thread"""
        input_path = self.input_input.text()
        output_path = self.output_dir_input.text()

        if not input_path or not os.path.exists(input_path):
            self.log_message("Invalid input path!")
            return

        if not output_path:
            self.log_message("Please select an output location!")
            return

        # Enforce .mp4 extension in single mode
        if not self.bulk_checkbox.isChecked():
            output_path_obj = Path(output_path)
            if output_path_obj.suffix.lower() != ".mp4":
                output_path = str(output_path_obj.with_suffix(".mp4"))
                self.output_dir_input.setText(output_path)

        # Disable the upscale button during processing
        self.upscale_button.setEnabled(False)

        # Clear log box and reset task duration
        self.log_box.clear()
        self.task_start_time = time.time()
        self.task_duration_label.setText("Task Duration: 00:00:00")

        # Create worker and thread
        self.worker = UpscaleWorker(
            input_path,
            output_path,
            int(self.scale_factor_input.currentText()),
            self.model_input.currentText(),
            self.batch_size_input.value(),
            bulk_mode=self.bulk_checkbox.isChecked()
        )
        self.worker_thread = threading.Thread(target=self.worker.run)

        # Connect signals
        self.worker.log_signal.connect(self.log_message)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.progress_signal.connect(self.update_progress)

        # Start the thread and timer
        self.task_timer.start(1000)  # Update every second
        self.worker_thread.start()

    def update_task_duration(self):
        """Update the task duration display"""
        if self.task_start_time:
            elapsed_time = time.time() - self.task_start_time
            hours, remainder = divmod(elapsed_time, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.task_duration_label.setText(
                f"Task Duration: {int(hours):02}:{int(minutes):02}:{int(seconds):02}"
            )

    def update_progress(self, percent, _):
        """Update progress bar with stage-based percentage"""
        self.progress_bar.setValue(percent)

    def on_finished(self):
        """Handle completion of the upscaling process"""
        self.upscale_button.setEnabled(True)
        self.progress_bar.setValue(100)
        self.update_task_duration()  # Final update
        self.task_timer.stop()

def main():
    """Run the GUI application."""
    app = QApplication(sys.argv)
    window = VUG()
    window.show()
    sys.exit(app.exec())
