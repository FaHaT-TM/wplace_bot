import tkinter as tk
from tkinter import ttk

from core.exceptions import RegionError
from core.screen_capture import validate_region
from gui.region_selector import RegionSelector


class SetupTab:
    """Setup tab for region selection and analysis"""
    
    def __init__(self, parent, main_window):
        self.parent = parent
        self.main_window = main_window
        self.data_manager = main_window.data_manager
        self.analysis_worker = main_window.analysis_worker
        self.message_queue = main_window.message_queue
        
        # Create the tab frame
        self.frame = ttk.Frame(parent)
        
        # UI elements that need to be accessed from main window
        self.canvas_status = None
        self.palette_status = None
        self.analyze_btn = None
        self.analysis_status = None
        self.tolerance_var = None
        self.delay_var = None
        self.tolerance_label = None
        self.delay_label = None
        
        # Create the UI
        self._create_ui()
        self._load_saved_regions()
    
    def _create_ui(self):
        """Create setup tab UI"""
        self._create_instructions()
        self._add_separator()
        self._create_region_selection()
        self._add_separator()
        self._create_analysis_frame()
        self._add_separator()
        self._create_settings_frame()
    
    def _create_instructions(self):
        """Create instructions frame"""
        instructions_frame = ttk.LabelFrame(self.frame, text="Instructions", padding=10)
        instructions_frame.pack(fill='x', padx=10, pady=5)
        
        instructions_text = """How to select regions:
1. Click 'Select Canvas' or 'Select Palette' button
2. Your screen will show a dark overlay
3. Click and drag to select the desired region
4. Release mouse to confirm selection
5. Press ESC to cancel selection"""
        
        self.instructions_label = ttk.Label(instructions_frame, text=instructions_text, justify='left')
        self.instructions_label.pack(anchor='w')
    
    def _create_region_selection(self):
        """Create region selection frame"""
        region_frame = ttk.LabelFrame(self.frame, text="Region Selection", padding=10)
        region_frame.pack(fill='x', padx=10, pady=5)
        
        # Canvas region
        canvas_frame = ttk.Frame(region_frame)
        canvas_frame.pack(fill='x', pady=5)
        ttk.Label(canvas_frame, text="Canvas Region:").pack(side='left')
        self.canvas_status = ttk.Label(canvas_frame, text="Not selected", foreground="red")
        self.canvas_status.pack(side='left', padx=(10, 0))
        canvas_btn = ttk.Button(canvas_frame, text="Select Canvas", 
                               command=self._select_canvas_region)
        canvas_btn.pack(side='right')
        self._create_tooltip(canvas_btn, "Click to select the drawing area on your screen")
        
        # Palette region
        palette_frame = ttk.Frame(region_frame)
        palette_frame.pack(fill='x', pady=5)
        ttk.Label(palette_frame, text="Palette Region:").pack(side='left')
        self.palette_status = ttk.Label(palette_frame, text="Not selected", foreground="red")
        self.palette_status.pack(side='left', padx=(10, 0))
        palette_btn = ttk.Button(palette_frame, text="Select Palette", 
                                command=self._select_palette_region)
        palette_btn.pack(side='right')
        self._create_tooltip(palette_btn, "Click to select the color palette area on your screen")
    
    def _create_analysis_frame(self):
        """Create analysis frame"""
        analysis_frame = ttk.LabelFrame(self.frame, text="Analysis", padding=10)
        analysis_frame.pack(fill='x', padx=10, pady=5)
        
        self.analyze_btn = ttk.Button(analysis_frame, text="Analyze Canvas & Palette", 
                                     command=self._analyze_regions, state='disabled')
        self.analyze_btn.pack(pady=5)
        self._create_tooltip(self.analyze_btn, "Analyze selected regions to detect pixels and colors")
        self.analysis_status = ttk.Label(analysis_frame, text="Select regions first")
        self.analysis_status.pack(pady=5)
    
    def _create_settings_frame(self):
        """Create settings frame"""
        settings_frame = ttk.LabelFrame(self.frame, text="Settings", padding=10)
        settings_frame.pack(fill='x', padx=10, pady=5)
        
        # Load saved settings
        saved_tolerance = self.data_manager.user_settings['preferences']['color_tolerance']
        saved_delay = self.data_manager.user_settings['preferences']['click_delay']
        
        # Tolerance setting
        tolerance_frame = ttk.Frame(settings_frame)
        tolerance_frame.pack(fill='x', pady=2)
        ttk.Label(tolerance_frame, text="Color Tolerance:").pack(side='left')
        self.tolerance_var = tk.IntVar(value=saved_tolerance)
        tolerance_scale = ttk.Scale(tolerance_frame, from_=1, to=20, variable=self.tolerance_var, 
                                   orient='horizontal', command=self._update_tolerance_label)
        tolerance_scale.pack(side='right', fill='x', expand=True, padx=(10, 0))
        self._create_tooltip(tolerance_scale, "How closely colors must match (1=exact, 20=loose)")
        self.tolerance_label = ttk.Label(tolerance_frame, text=str(saved_tolerance))
        self.tolerance_label.pack(side='right', padx=(5, 10))
        
        # Click delay setting
        delay_frame = ttk.Frame(settings_frame)
        delay_frame.pack(fill='x', pady=2)
        ttk.Label(delay_frame, text="Click Delay (ms):").pack(side='left')
        self.delay_var = tk.IntVar(value=saved_delay)
        delay_scale = ttk.Scale(delay_frame, from_=10, to=100, variable=self.delay_var, 
                               orient='horizontal', command=self._update_delay_label)
        delay_scale.pack(side='right', fill='x', expand=True, padx=(10, 0))
        self._create_tooltip(delay_scale, "Delay between mouse clicks in milliseconds (lower=faster)")
        self.delay_label = ttk.Label(delay_frame, text=str(saved_delay))
        self.delay_label.pack(side='right', padx=(5, 10))
    
    def refresh_fonts(self):
        """Refresh fonts when scaling changes"""
        if hasattr(self, 'instructions_label'):
            font = self.main_window.get_scaled_font(9)
            self.instructions_label.config(font=font)
    
    def _load_saved_regions(self):
        """Load and validate saved regions against current screen bounds."""
        screen_size = (
            self.main_window.root.winfo_screenwidth(),
            self.main_window.root.winfo_screenheight(),
        )
        if self.data_manager.canvas_region is not None:
            try:
                valid_canvas = validate_region(
                    self.data_manager.canvas_region,
                    name="Canvas region",
                    screen_size=screen_size,
                )
                self.data_manager.canvas_region = valid_canvas
                self.canvas_status.config(
                    text=f"Loaded: {valid_canvas}", foreground="blue"
                )
            except RegionError:
                self.data_manager.canvas_region = None
                self.canvas_status.config(
                    text="Saved region out of screen bounds - reselect",
                    foreground="orange",
                )
        if self.data_manager.palette_region is not None:
            try:
                valid_palette = validate_region(
                    self.data_manager.palette_region,
                    name="Palette region",
                    screen_size=screen_size,
                )
                self.data_manager.palette_region = valid_palette
                self.palette_status.config(
                    text=f"Loaded: {valid_palette}", foreground="blue"
                )
            except RegionError:
                self.data_manager.palette_region = None
                self.palette_status.config(
                    text="Saved region out of screen bounds - reselect",
                    foreground="orange",
                )
        self._check_ready_for_analysis()
    
    def _select_canvas_region(self):
        """Start canvas region selection"""
        self.main_window.root.withdraw()
        RegionSelector(self._on_canvas_region_selected)
    
    def _select_palette_region(self):
        """Start palette region selection"""
        self.main_window.root.withdraw()
        RegionSelector(self._on_palette_region_selected)
    
    def _on_canvas_region_selected(self, region):
        """Handle canvas region selection"""
        self.main_window.root.deiconify()
        if region:
            self.data_manager.set_canvas_region(region)
            self.canvas_status.config(text=f"Selected: {region}", foreground="green")
        else:
            self.main_window.log_message("Canvas region selection canceled")
        self._check_ready_for_analysis()
    
    def _on_palette_region_selected(self, region):
        """Handle palette region selection"""
        self.main_window.root.deiconify()
        if region:
            self.data_manager.set_palette_region(region)
            self.palette_status.config(text=f"Selected: {region}", foreground="green")
        else:
            self.main_window.log_message("Palette region selection canceled")
        self._check_ready_for_analysis()
    
    def _check_ready_for_analysis(self):
        """Enable analysis button if both regions are selected"""
        if self.data_manager.canvas_region and self.data_manager.palette_region:
            self.analyze_btn.config(state='normal')
    
    def _analyze_regions(self):
        """Run analysis in a separate thread"""
        self.analyze_btn.config(state='disabled', text="Analyzing...")
        self.analysis_status.config(text="Running analysis...")
        self.main_window.log_message("Starting analysis...")
        
        # Use the analysis worker
        self.analysis_worker.start_analysis(self.message_queue)
    
    def _update_tolerance_label(self, value):
        """Update tolerance value display and save"""
        self.tolerance_label.config(text=f"{int(float(value))}")
        self._debounced_save()

    def _update_delay_label(self, value):
        """Update delay value display and save"""
        self.delay_label.config(text=f"{int(float(value))}")
        self._debounced_save()
    
    def _debounced_save(self):
        """Save settings with debouncing to prevent UI freezing"""
        if hasattr(self, '_save_timer'):
            self.main_window.root.after_cancel(self._save_timer)
        self._save_timer = self.main_window.root.after(500, self.main_window.save_user_settings)
    
    def on_analysis_complete(self, message):
        """Handle analysis completion from main window"""
        self.analyze_btn.config(state='normal', text="Analyze Canvas & Palette")
        self.analysis_status.config(
            text=f"Analysis complete! Pixel size: {message['pixel_size']}, "
                 f"Pixels: {message['pixel_count']}, Colors: {message['colors_found']}"
        )
    
    def _create_tooltip(self, widget, text):
        """Create modern styled tooltip for widget"""
        def on_enter(event):
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{event.x_root+15}+{event.y_root+10}")
            tooltip.configure(bg='#2d2d2d')
            
            # Add subtle shadow effect with frame
            shadow_frame = tk.Frame(tooltip, bg='#1a1a1a', bd=0)
            shadow_frame.pack(fill='both', expand=True, padx=(2, 0), pady=(2, 0))
            
            main_frame = tk.Frame(shadow_frame, bg='#2d2d2d', bd=1, relief='solid')
            main_frame.pack(fill='both', expand=True, padx=(0, 2), pady=(0, 2))
            
            label = tk.Label(main_frame, text=text, 
                           bg='#2d2d2d', fg='#ffffff',
                           font=('Segoe UI', 9), 
                           padx=8, pady=4)
            label.pack()
            widget.tooltip = tooltip
        
        def on_leave(event):
            if hasattr(widget, 'tooltip'):
                widget.tooltip.destroy()
                del widget.tooltip
        
        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)
    
    def _add_separator(self):
        """Add visual separator"""
        ttk.Separator(self.frame, orient='horizontal').pack(fill='x', padx=20, pady=8)
    
    def on_analysis_error(self, message):
        """Handle analysis error from main window"""
        self.analyze_btn.config(state='normal', text="Analyze Canvas & Palette")
        self.analysis_status.config(text=f"Analysis failed: {message['error']}")