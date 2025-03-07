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
)
from PySide6.QtCore import QTimer, Qt, Signal, QObject


# Supported video file extensions
SUPPORTED_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".mpeg", ".mpg")


class UpscaleWorker(QObject):
    log_signal = Signal(str)  # Signal to send log messages
    finished_signal = Signal()  # Signal to indicate process completion

    def __init__(self, input_path, output_dir, scale, model, batch_size):
        super().__init__()
        self.input_path = input_path
        self.output_dir = output_dir
        self.scale = scale
        self.model = model
        self.batch_size = batch_size

    def run(self):
        """Run the upscaling process in a separate thread"""
        try:
            if os.path.isfile(self.input_path):
                # Single file mode
                self.upscale_single_video(self.input_path, self.output_dir)
            elif os.path.isdir(self.input_path):
                # Directory mode
                self.upscale_directory(self.input_path, self.output_dir)
            else:
                raise ValueError("Input path is neither a file nor a directory")
        except Exception as e:
            self.log_signal.emit(f"Error: {str(e)}")
        finally:
            self.finished_signal.emit()

    def upscale_single_video(self, input_video, output_dir):
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

        # Create output directory if it doesn't exist
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_file = os.path.join(
            output_dir,
            f"upscaled_{Path(input_video).stem}.mp4"
        )

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
            self.upscale_single_video(video_file, output_subdir)

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

    def upscale_frames(self, input_dir, output_dir, scale, model, batch_size):
        """Upscale frames and log output"""
        process = subprocess.Popen(
            [
                "realesrgan-ncnn-vulkan",
                "-i", input_dir,
                "-o", output_dir,
                "-s", str(scale),
                "-n", model,
                "-j", f"{batch_size}:{batch_size}:{batch_size}",  # Use batch size for all stages
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for line in process.stderr:
            self.log_signal.emit(line.strip())

    def combine_frames(self, frames_dir, output_file, fps, input_video):
        """Combine frames into video and log output"""
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
        for line in process.stderr:
            self.log_signal.emit(line.strip())


class VUG(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VUG")
        self.setGeometry(100, 100, 600, 500)
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.layout = QVBoxLayout()
        self.main_widget.setLayout(self.layout)

        # Input Video/Directory
        self.input_label = QLabel("Input Video/Directory:")
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
        self.output_dir_label = QLabel("Output Directory:")
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setReadOnly(True)
        self.output_dir_button = QPushButton("Browse...")
        self.output_dir_button.clicked.connect(self.select_output_dir)
        output_dir_box = QHBoxLayout()
        output_dir_box.addWidget(self.output_dir_label)
        output_dir_box.addWidget(self.output_dir_input)
        output_dir_box.addWidget(self.output_dir_button)
        self.layout.addLayout(output_dir_box)

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

        # Spinner and Task Duration
        self.spinner_label = QLabel("")
        self.spinner_label.setAlignment(Qt.AlignCenter)
        self.task_duration_label = QLabel("Task Duration: 00:00:00")
        self.task_duration_label.setAlignment(Qt.AlignCenter)
        spinner_duration_box = QHBoxLayout()
        spinner_duration_box.addWidget(self.spinner_label)
        spinner_duration_box.addWidget(self.task_duration_label)
        self.layout.addLayout(spinner_duration_box)

        # Timer for spinner animation
        self.spinner_timer = QTimer()
        self.spinner_timer.timeout.connect(self.update_spinner)
        self.spinner_frames = ["|", "/", "-", "\\"]
        self.spinner_index = 0

        # Task start time
        self.task_start_time = None

        # Worker for upscaling process
        self.worker = None
        self.worker_thread = None

    def select_input(self):
        """Open file/directory dialog to select input"""
        dialog = QFileDialog()
        dialog.setFileMode(QFileDialog.ExistingFiles)  # Allow selecting multiple files or directories
        dialog.setNameFilter(
            "Videos (*.mp4 *.mov *.avi *.mkv *.flv *.wmv *.webm *.mpeg *.mpg);;"
            "All Files (*)"
        )
        if dialog.exec():
            selected_paths = dialog.selectedFiles()
            if selected_paths:
                self.input_input.setText(selected_paths[0])  # Display the first selected path
                # Set default output directory to input's parent
                input_path = Path(selected_paths[0])
                self.output_dir_input.setText(str(input_path.parent))

    def select_output_dir(self):
        """Open directory dialog to select output location"""
        dir_dialog = QFileDialog()
        dir_dialog.setFileMode(QFileDialog.Directory)
        if dir_dialog.exec():
            selected_dirs = dir_dialog.selectedFiles()
            if selected_dirs:
                self.output_dir_input.setText(selected_dirs[0])

    def log_message(self, message):
        """Append a message to the log box"""
        self.log_box.append(message)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def update_spinner(self):
        """Update spinner animation and task duration"""
        self.spinner_label.setText(self.spinner_frames[self.spinner_index])
        self.spinner_index = (self.spinner_index + 1) % len(self.spinner_frames)

        # Update task duration
        if self.task_start_time:
            elapsed_time = time.time() - self.task_start_time
            hours, remainder = divmod(elapsed_time, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.task_duration_label.setText(
                f"Task Duration: {int(hours):02}:{int(minutes):02}:{int(seconds):02}"
            )

    def upscale_video(self):
        """Start the upscaling process in a separate thread"""
        input_path = self.input_input.text()
        output_dir = self.output_dir_input.text()

        if not input_path or not os.path.exists(input_path):
            self.log_message("Invalid input path!")
            return

        if not output_dir:
            self.log_message("Please select an output directory!")
            return

        # Disable the upscale button during processing
        self.upscale_button.setEnabled(False)

        # Clear log box and reset task duration
        self.log_box.clear()
        self.task_start_time = time.time()
        self.task_duration_label.setText("Task Duration: 00:00:00")

        # Start spinner animation
        self.spinner_timer.start(200)

        # Create worker and thread
        self.worker = UpscaleWorker(
            input_path,
            output_dir,
            int(self.scale_factor_input.currentText()),
            self.model_input.currentText(),
            self.batch_size_input.value(),  # Pass batch size to worker
        )
        self.worker_thread = threading.Thread(target=self.worker.run)

        # Connect signals
        self.worker.log_signal.connect(self.log_message)
        self.worker.finished_signal.connect(self.on_finished)

        # Start the thread
        self.worker_thread.start()

    def on_finished(self):
        """Handle completion of the upscaling process"""
        self.spinner_timer.stop()
        self.spinner_label.setText("")
        self.upscale_button.setEnabled(True)

        # Final task duration update
        if self.task_start_time:
            elapsed_time = time.time() - self.task_start_time
            hours, remainder = divmod(elapsed_time, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.task_duration_label.setText(
                f"Task Duration: {int(hours):02}:{int(minutes):02}:{int(seconds):02}"
            )

def main():
    """Run the GUI application."""
    app = QApplication(sys.argv)
    window = VUG()
    window.show()
    sys.exit(app.exec())
