// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
  #[cfg(target_os = "linux")]
  {
    // Prevent WebKitGTK / Mesa Gallium crash on Linux Wayland/AMD
    if std::env::var("WEBKIT_DISABLE_DMABUF_RENDERER").is_err() {
      // Set before any threads or WebKit/GTK initialization.
      unsafe {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
      }
    }
  }

  app_lib::run();
}
