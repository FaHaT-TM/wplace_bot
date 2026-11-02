import logging
import tkinter as tk
from tkinter import ttk
from typing import Any

logger = logging.getLogger(__name__)

class ColorsTab:
    """Color control tab for enabling/disabling colors"""
    
    def __init__(self, parent, main_window):
        self.parent = parent
        self.main_window = main_window
        self.data_manager = main_window.data_manager
        
        # Create the tab frame
        self.frame = ttk.Frame(parent)
        
        # UI variables
        self.color_vars = {}
        self.bought_vars = {}
        self.color_labels = {}
        self.color_widgets = []
        self.sort_method = 'id'  # Default sort method
        self.profile_var = None
        
        # Undo system - per profile
        self.profile_undo_history = {}  # {profile_name: [states]}
        self.undo_button = None
        
        # Create the UI
        self._create_ui()
    
    def _create_ui(self):
        """Create colors tab UI"""
        self._create_scrollable_frame()
        self._create_profile_controls()
        self._add_separator()
        self._create_control_buttons()
        self._add_separator()
        self._create_color_widgets()
    
    def _create_scrollable_frame(self):
        """Create scrollable frame for color widgets"""
        # Create scrollable frame
        self.canvas = tk.Canvas(self.frame)
        self.scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)
        
        self.scrollable_frame.bind("<Configure>", 
                                 lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # Mouse wheel scrolling
        def on_mousewheel(event):
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        def on_enter(event):
            self.canvas.focus_set()
        
        # Store the mousewheel handler for use in widgets
        self.on_mousewheel = on_mousewheel
        
        # Bind mouse wheel events
        for widget in [self.canvas, self.scrollable_frame, self.frame]:
            widget.bind("<MouseWheel>", on_mousewheel)
        self.canvas.bind("<Enter>", on_enter)
    
    def _create_profile_controls(self):
        """Create profile management controls"""
        profile_frame = ttk.Frame(self.frame)
        profile_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Label(profile_frame, text="Profile:").pack(side='left', padx=5)
        
        self.profile_var = tk.StringVar(value=self.data_manager.get_active_profile())
        profile_combo = ttk.Combobox(profile_frame, textvariable=self.profile_var,
                                    values=self.data_manager.get_profile_names(),
                                    state='readonly', width=15)
        profile_combo.pack(side='left', padx=5)
        profile_combo.bind('<<ComboboxSelected>>', self._on_profile_change)
        self._create_tooltip(profile_combo, "Switch between different color profiles")
        
        new_btn = ttk.Button(profile_frame, text="New", command=self._create_new_profile)
        new_btn.pack(side='left', padx=2)
        self._create_tooltip(new_btn, "Create a new color profile")
        
        rename_btn = ttk.Button(profile_frame, text="Rename", command=self._rename_profile)
        rename_btn.pack(side='left', padx=2)
        self._create_tooltip(rename_btn, "Rename the current profile")
        
        delete_btn = ttk.Button(profile_frame, text="Delete", command=self._delete_profile)
        delete_btn.pack(side='left', padx=2)
        self._create_tooltip(delete_btn, "Delete the current profile")
    
    def _create_control_buttons(self):
        """Create control buttons"""
        control_frame = ttk.Frame(self.frame)
        control_frame.pack(fill='x', padx=10, pady=5)
        
        # First row - enable/disable buttons
        button_row1 = ttk.Frame(control_frame)
        button_row1.pack(fill='x', pady=2)
        
        buttons = [
            ("Enable All", self.enable_all_colors),
            ("Disable All", self.disable_all_colors),
            ("Free Colors", self.enable_only_free),
            ("Available Colors", self.enable_available_colors)
        ]
        
        button_tooltips = [
            "Enable all colors for painting",
            "Disable all colors (none will be used)",
            "Enable only free colors (no premium)",
            "Enable free colors + bought premium colors"
        ]
        
        for (text, command), tooltip in zip(buttons, button_tooltips):
            btn = ttk.Button(button_row1, text=text, command=command)
            btn.pack(side='left', padx=5)
            self._create_tooltip(btn, tooltip)
        
        # Undo button (initially hidden)
        self.undo_button = ttk.Button(button_row1, text="Undo", command=self.undo_last_change, state='disabled')
        self.undo_button.pack(side='left', padx=5)
        self._create_tooltip(self.undo_button, "Undo last bulk color operation")
        
        # Second row - sort controls
        sort_row = ttk.Frame(control_frame)
        sort_row.pack(fill='x', pady=2)
        
        ttk.Label(sort_row, text="Sort by:").pack(side='left', padx=5)
        
        self.sort_var = tk.StringVar(value='id')
        sort_combo = ttk.Combobox(sort_row, textvariable=self.sort_var, 
                                 values=['id', 'name', 'premium', 'grayscale'], 
                                 state='readonly', width=12)
        sort_combo.pack(side='left', padx=5)
        sort_combo.bind('<<ComboboxSelected>>', self._on_sort_change)
        self._create_tooltip(sort_combo, "Sort colors by: ID, name, premium status, or brightness")
    
    def _create_color_widgets(self):
        """Create color control widgets"""
        sorted_colors = self._get_sorted_colors()
        for color in sorted_colors:
            self._create_single_color_widget(color)
        
        # Pack canvas and scrollbar after all widgets are created
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
    
    def _create_single_color_widget(self, color):
        """Create a single color widget"""
        color_frame = ttk.Frame(self.scrollable_frame)
        color_frame.pack(fill='x', padx=10, pady=2)
        color_frame.bind("<MouseWheel>", self.on_mousewheel)
        
        # Color preview
        color_canvas = tk.Canvas(color_frame, width=30, height=20)
        color_canvas.pack(side='left', padx=5)
        color_canvas.bind("<MouseWheel>", self.on_mousewheel)
        
        rgb = color['rgb']
        hex_color = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
        color_canvas.create_rectangle(0, 0, 30, 20, fill=hex_color, outline="")
        
        # Load saved state from active profile
        color_id = str(color['id'])
        is_bought = self.data_manager.get_color_setting(color_id, 'bought', False) if color.get('premium', False) else True
        default_enabled = not color.get('premium', False) or is_bought
        is_enabled = self.data_manager.get_color_setting(color_id, 'enabled', default_enabled)
        
        # Enable/disable checkbox
        var = tk.BooleanVar(value=is_enabled)
        self.color_vars[color['name']] = var
        checkbox = ttk.Checkbutton(color_frame, variable=var, command=self._on_color_setting_change)
        checkbox.pack(side='left', padx=5)
        checkbox.bind("<MouseWheel>", self.on_mousewheel)
        
        # Color name label
        label_text = f"{color['name']} ({'Premium' if color.get('premium', False) else 'Free'})"
        label_color = "green" if (not color.get('premium', False) or is_bought) else "red"
        
        name_label = ttk.Label(color_frame, text=label_text, foreground=label_color)
        name_label.pack(side='left', padx=5)
        name_label.bind("<MouseWheel>", self.on_mousewheel)
        
        # Store label reference for updates
        self.color_labels[color['name']] = name_label
        
        # Bought toggle for premium colors
        if color.get('premium', False):
            bought_var = tk.BooleanVar(value=is_bought)
            self.bought_vars[color['name']] = bought_var
            
            bought_checkbox = ttk.Checkbutton(
                color_frame, text="Bought", variable=bought_var,
                command=lambda c=color: self._toggle_bought_status(c)
            )
            bought_checkbox.pack(side='left', padx=10)
            bought_checkbox.bind("<MouseWheel>", self.on_mousewheel)
    
    def _on_color_setting_change(self):
        """Handle color setting changes with debouncing"""
        # Use a delayed save to avoid too frequent saves
        if hasattr(self, '_save_timer'):
            self.main_window.root.after_cancel(self._save_timer)
        self._save_timer = self.main_window.root.after(500, self.main_window.save_user_settings)
    
    def _toggle_bought_status(self, color):
        """Toggle bought status and save"""
        try:
            color_name = color['name']
            color_id = str(color['id'])
            new_bought_status = self.bought_vars[color_name].get()
            
            # Update using data_manager
            self.data_manager.set_color_setting(color_id, 'bought', new_bought_status)
            self._update_color_label(color, new_bought_status)
            
        except Exception:
            logger.exception("Failed to toggle bought status for %s", color.get("name"))
    
    def _update_color_label(self, color, is_bought):
        """Update specific color label"""
        color_name = color['name']
        
        if color_name in self.color_labels:
            label_widget = self.color_labels[color_name]
            label_text = f"{color_name} ({'Premium' if color.get('premium', False) else 'Free'})"
            label_color = "green" if (not color.get('premium', False) or is_bought) else "red"
            label_widget.config(text=label_text, foreground=label_color)
    
    def enable_all_colors(self):
        """Enable all colors"""
        self._save_current_state()
        for var in self.color_vars.values():
            var.set(True)
        self._show_undo_button()
        self.main_window.save_user_settings()
    
    def disable_all_colors(self):
        """Disable all colors"""
        self._save_current_state()
        for var in self.color_vars.values():
            var.set(False)
        self._show_undo_button()
        self.main_window.save_user_settings()
    
    def enable_only_free(self):
        """Enable only free colors"""
        self._save_current_state()
        for color in self.data_manager.color_palette:
            is_free = not color.get('premium', False)
            if color['name'] in self.color_vars:
                self.color_vars[color['name']].set(is_free)
        self._show_undo_button()
        self.main_window.save_user_settings()
    
    def enable_available_colors(self):
        """Enable all available colors (free + bought premium colors)"""
        self._save_current_state()
        for color in self.data_manager.color_palette:
            color_id = str(color['id'])
            is_bought = self.data_manager.get_color_setting(color_id, 'bought', False) if color.get('premium', False) else True
            is_available = not color.get('premium', False) or is_bought
            
            if color['name'] in self.color_vars:
                self.color_vars[color['name']].set(is_available)
        self._show_undo_button()
        self.main_window.save_user_settings()
    
    def _get_sorted_colors(self):
        """Get colors sorted by current method"""
        colors = self.data_manager.color_palette.copy()
        
        if self.sort_method == 'id':
            return sorted(colors, key=lambda c: c['id'])
        elif self.sort_method == 'name':
            return sorted(colors, key=lambda c: c['name'])
        elif self.sort_method == 'premium':
            return sorted(colors, key=lambda c: (c.get('premium', False), c['id']))
        elif self.sort_method == 'grayscale':
            return sorted(colors, key=lambda c: self._get_grayscale_value(c['rgb']))
        
        return colors
    
    def _get_grayscale_value(self, rgb):
        """Calculate grayscale value using luminance formula"""
        r, g, b = rgb
        return 0.299 * r + 0.587 * g + 0.114 * b
    
    def _on_sort_change(self, event=None):
        """Handle sort method change"""
        self.sort_method = self.sort_var.get()
        self._sort_colors()
    
    def _sort_colors(self):
        """Re-sort and recreate color widgets"""
        # Clear existing widgets
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        
        # Recreate with new sort order
        sorted_colors = self._get_sorted_colors()
        for color in sorted_colors:
            self._create_single_color_widget(color)
    
    def _on_profile_change(self, event=None):
        """Handle profile selection change"""
        new_profile = self.profile_var.get()
        if self.data_manager.switch_profile(new_profile):
            self._refresh_color_widgets()
            self._update_undo_button_state()
    
    def _create_new_profile(self):
        """Create new profile dialog with options"""
        dialog = ProfileCreationDialog(self.main_window.root, self.data_manager)
        result = dialog.show()
        
        if result:
            name, copy_from = result
            success = False
            
            if copy_from:
                success = self.data_manager.copy_profile(copy_from, name)
            else:
                success = self.data_manager.create_profile(name)
            
            if success:
                self._update_profile_combo()
                self.profile_var.set(name)
                self.data_manager.switch_profile(name)
                self._refresh_color_widgets()
    
    def _rename_profile(self):
        """Rename current profile dialog"""
        from tkinter import simpledialog
        current = self.profile_var.get()
        if current == 'Default':
            from tkinter import messagebox
            messagebox.showwarning("Warning", "Cannot rename Default profile")
            return
        
        new_name = simpledialog.askstring("Rename Profile", f"Rename '{current}' to:", initialvalue=current)
        if new_name and self.data_manager.rename_profile(current, new_name):
            self._update_profile_combo()
            self.profile_var.set(new_name)
    
    def _delete_profile(self):
        """Delete current profile dialog"""
        from tkinter import messagebox
        current = self.profile_var.get()
        if current == 'Default':
            messagebox.showwarning("Warning", "Cannot delete Default profile")
            return
        
        if messagebox.askyesno(
            "Delete Profile", f"Delete profile '{current}'?"
        ) and self.data_manager.delete_profile(current):
            self._update_profile_combo()
            self.profile_var.set("Default")
            self._refresh_color_widgets()
    
    def _update_profile_combo(self):
        """Update profile combobox values"""
        # Find the combobox widget and update its values
        for widget in self.frame.winfo_children():
            if isinstance(widget, ttk.Frame):
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Combobox) and child['textvariable'] == str(self.profile_var):
                        child['values'] = self.data_manager.get_profile_names()
                        break
    
    def _refresh_color_widgets(self):
        """Refresh color widgets after profile change"""
        # Clear existing widgets
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        
        # Clear variables
        self.color_vars.clear()
        self.bought_vars.clear()
        self.color_labels.clear()
        
        # Recreate widgets with new profile data
        sorted_colors = self._get_sorted_colors()
        for color in sorted_colors:
            self._create_single_color_widget(color)
    
    def _save_current_state(self):
        """Save current color state for undo"""
        profile = self.data_manager.get_active_profile()
        if profile not in self.profile_undo_history:
            self.profile_undo_history[profile] = []
        
        state = {}
        for name, var in self.color_vars.items():
            state[name] = var.get()
        
        self.profile_undo_history[profile].append(state)
        if len(self.profile_undo_history[profile]) > 5:  # Keep last 5 states
            self.profile_undo_history[profile].pop(0)
    
    def _show_undo_button(self):
        """Show and enable undo button"""
        self._update_undo_button_state()
    
    def _update_undo_button_state(self):
        """Update undo button state based on current profile history"""
        if not self.undo_button:
            return
        
        profile = self.data_manager.get_active_profile()
        has_history = (profile in self.profile_undo_history and 
                      len(self.profile_undo_history[profile]) > 0)
        
        self.undo_button.config(state='normal' if has_history else 'disabled')
    
    def undo_last_change(self):
        """Restore previous color state"""
        profile = self.data_manager.get_active_profile()
        if profile not in self.profile_undo_history or not self.profile_undo_history[profile]:
            return
        
        last_state = self.profile_undo_history[profile].pop()
        for name, value in last_state.items():
            if name in self.color_vars:
                self.color_vars[name].set(value)
        
        # Update undo button state
        self._update_undo_button_state()
        self.main_window.save_user_settings()
    
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
    
    def get_enabled_colors(self) -> list[dict[str, Any]]:
        """Get list of enabled and paintable (free or bought premium) colors."""
        enabled_colors: list[dict[str, Any]] = []
        for color in self.data_manager.color_palette:
            color_name = color["name"]
            color_id = str(color["id"])
            is_enabled = (
                self.color_vars[color_name].get()
                if color_name in self.color_vars
                else self.data_manager.get_color_setting(color_id, "enabled", True)
            )
            if not is_enabled:
                continue

            if color.get("premium", False):
                is_bought = (
                    self.bought_vars[color_name].get()
                    if color_name in self.bought_vars
                    else self.data_manager.is_color_bought(color)
                )
                if not is_bought:
                    continue

            enabled_colors.append(color)
        return enabled_colors


class ProfileCreationDialog:
    """Dialog for creating new profiles with copy option"""
    
    def __init__(self, parent, data_manager):
        self.parent = parent
        self.data_manager = data_manager
        self.result = None
    
    def show(self):
        """Show dialog and return (name, copy_from) or None"""
        import tkinter as tk
        from tkinter import messagebox, ttk
        
        dialog = tk.Toplevel(self.parent)
        dialog.title("Create New Profile")
        dialog.geometry("400x250")
        dialog.resizable(False, False)
        dialog.transient(self.parent)
        dialog.grab_set()
        
        # Center dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (400 // 2)
        y = (dialog.winfo_screenheight() // 2) - (250 // 2)
        dialog.geometry(f"400x250+{x}+{y}")
        
        # Main frame with padding
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill='both', expand=True)
        
        # Name entry
        name_frame = ttk.Frame(main_frame)
        name_frame.pack(fill='x', pady=(0, 15))
        ttk.Label(name_frame, text="Profile Name:").pack(anchor='w')
        name_var = tk.StringVar()
        name_entry = ttk.Entry(name_frame, textvariable=name_var, width=35)
        name_entry.pack(fill='x', pady=(5, 0))
        name_entry.focus()
        
        # Copy option
        copy_frame = ttk.Frame(main_frame)
        copy_frame.pack(fill='x', pady=(0, 15))
        copy_var = tk.BooleanVar()
        copy_cb = ttk.Checkbutton(copy_frame, text="Copy from existing profile", variable=copy_var)
        copy_cb.pack(anchor='w')
        
        # Source profile selection
        source_frame = ttk.Frame(main_frame)
        source_frame.pack(fill='x', pady=(0, 20))
        ttk.Label(source_frame, text="Copy from:").pack(anchor='w')
        source_var = tk.StringVar(value=self.data_manager.get_active_profile())
        source_combo = ttk.Combobox(source_frame, textvariable=source_var,
                                   values=self.data_manager.get_profile_names(),
                                   state='readonly', width=32)
        source_combo.pack(fill='x', pady=(5, 0))
        
        def on_copy_toggle():
            source_combo.config(state='readonly' if copy_var.get() else 'disabled')
        
        copy_cb.config(command=on_copy_toggle)
        on_copy_toggle()  # Initial state
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x')
        
        def on_create():
            name = name_var.get().strip()
            if not name:
                messagebox.showerror("Error", "Please enter a profile name")
                return
            
            if name in self.data_manager.get_profile_names():
                messagebox.showerror("Error", f"Profile '{name}' already exists")
                return
            
            copy_from = source_var.get() if copy_var.get() else None
            self.result = (name, copy_from)
            dialog.destroy()
        
        def on_cancel():
            self.result = None
            dialog.destroy()
        
        ttk.Button(button_frame, text="Create", command=on_create).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Cancel", command=on_cancel).pack(side='left', padx=5)
        
        # Enter key binding
        dialog.bind('<Return>', lambda e: on_create())
        dialog.bind('<Escape>', lambda e: on_cancel())
        
        dialog.wait_window()
        return self.result