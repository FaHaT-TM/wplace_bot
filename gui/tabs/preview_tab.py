import os
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk


class PreviewTab:
    """Preview tab for showing debug images and analysis results"""
    
    def __init__(self, parent, main_window):
        self.parent = parent
        self.main_window = main_window
        self.data_manager = main_window.data_manager
        
        # Create the tab frame
        self.frame = ttk.Frame(parent)
        
        # UI elements
        self.image_var = None
        self.image_label = None
        self.stats_text = None
        
        # Create the UI
        self._create_ui()
    
    def _create_ui(self):
        """Create preview tab UI"""
        self._create_image_frame()
        self._create_stats_frame()
    
    def _create_image_frame(self):
        """Create image display frame"""
        image_frame = ttk.LabelFrame(self.frame, text="Debug Images", padding=10)
        image_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Image selection
        img_select_frame = ttk.Frame(image_frame)
        img_select_frame.pack(fill='x', pady=5)
        
        self.image_var = tk.StringVar()
        image_combo = ttk.Combobox(img_select_frame, textvariable=self.image_var,
                                  values=["Size Estimation", "Palette Detection"],
                                  state="readonly")
        image_combo.pack(side='left', padx=5)
        image_combo.bind('<<ComboboxSelected>>', self._load_debug_image)
        
        ttk.Button(img_select_frame, text="Refresh", 
                  command=self._refresh_debug_images).pack(side='left', padx=5)
        
        self.image_label = ttk.Label(image_frame, text="No image selected")
        self.image_label.pack(expand=True)
    
    def _create_stats_frame(self):
        """Create statistics frame"""
        stats_frame = ttk.LabelFrame(self.frame, text="Analysis Results", padding=10)
        stats_frame.pack(fill='x', padx=10, pady=5)
        
        self.stats_text = tk.Text(stats_frame, height=8, state='disabled')
        stats_scrollbar = ttk.Scrollbar(stats_frame, command=self.stats_text.yview)
        self.stats_text.config(yscrollcommand=stats_scrollbar.set)
        
        self.stats_text.pack(side='left', fill='both', expand=True)
        stats_scrollbar.pack(side='right', fill='y')
    
    def _load_debug_image(self, event=None):
        """Load and display debug image"""
        image_type = self.image_var.get()
        filename_map = {
            "Size Estimation": "debug_size_estimation.png",
            "Palette Detection": "debug_palette.png"
        }
        
        filename = filename_map.get(image_type)
        if filename and os.path.exists(filename):
            try:
                img = Image.open(filename)
                img.thumbnail((600, 400), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.image_label.config(image=photo, text="")
                self.image_label.image = photo
            except (OSError, tk.TclError) as e:
                self.image_label.config(text=f"Error loading image: {e}")
        else:
            self.image_label.config(text="Image not found. Run analysis first.")
    
    def _refresh_debug_images(self):
        """Refresh debug image list"""
        self._load_debug_image()
    
    def update_stats(self):
        """Update statistics display"""
        if not self.data_manager.has_analysis_data():
            return
        
        # Get settings from other tabs
        setup_tab = self.main_window.setup_tab
        colors_tab = self.main_window.colors_tab
        control_tab = self.main_window.control_tab
        
        tolerance = (
            setup_tab.tolerance_var.get()
            if setup_tab
            else self.data_manager.get_preference("color_tolerance", 1)
        )
        delay = (
            setup_tab.delay_var.get()
            if setup_tab
            else self.data_manager.get_preference("click_delay", 15)
        )
        pixel_limit = (
            control_tab.get_pixel_limit_or_last_valid()
            if control_tab
            else self.data_manager.get_preference("pixel_limit", 1000)
        )
        
        enabled_count = len([var for var in colors_tab.color_vars.values() if var.get()]) if colors_tab else 0
        total_count = len(self.data_manager.color_palette)
        
        stats = f"""Analysis Results:
        
Pixel Size: {self.data_manager.pixel_size}x{self.data_manager.pixel_size}
Total Pixels Detected: {len(self.data_manager.pixel_map)}
Colors Found in Palette: {len(self.data_manager.color_position_map)}

Enabled Colors: {enabled_count}
Total Colors: {total_count}

Settings:
Color Tolerance: {tolerance}
Click Delay: {delay}ms
Pixel Limit: {pixel_limit}
"""
        
        self.stats_text.config(state='normal')
        self.stats_text.delete(1.0, tk.END)
        self.stats_text.insert(1.0, stats)
        self.stats_text.config(state='disabled')