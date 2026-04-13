//! @file_name: tray.rs
//! @description: Tauri command `set_tray_badge` — update tray icon title to
//! reflect the current dashboard running_count.
//!
//! Contract with frontend (`frontend/src/lib/tauri.ts::setTrayBadge`):
//! - `count == 0` → clear title (no badge shown)
//! - `1 ≤ count ≤ 999` → title is the count as string
//! - `count > 999` → title is "999+"
//!
//! All paths are best-effort: failures (lock contention, tray not yet built)
//! are swallowed because the tray badge is cosmetic. This command never
//! panics and never returns Err for expected conditions.

use tauri::State;

use crate::state::AppState;

#[tauri::command]
pub fn set_tray_badge(count: u32, state: State<AppState>) -> Result<(), String> {
    let title = clamp_display(count);
    match state.tray_handle.try_lock() {
        Ok(guard) => {
            if let Some(tray) = guard.as_ref() {
                // Tauri v2: set_title(Option<&str>) — None clears, Some(&str) sets
                let title_opt: Option<&str> = if title.is_empty() { None } else { Some(&title) };
                if let Err(e) = tray.set_title(title_opt) {
                    log::debug!("set_tray_badge: set_title failed (cosmetic): {}", e);
                }
            }
            Ok(())
        }
        Err(_) => {
            // Lock contention — best-effort, skip this update.
            Ok(())
        }
    }
}

/// Return empty string to clear, otherwise the display text ("1".."999" or "999+").
fn clamp_display(count: u32) -> String {
    if count == 0 {
        String::new()
    } else if count > 999 {
        "999+".to_string()
    } else {
        format!("{}", count)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clamp_zero() {
        assert_eq!(clamp_display(0), "");
    }

    #[test]
    fn clamp_one() {
        assert_eq!(clamp_display(1), "1");
    }

    #[test]
    fn clamp_max_in_range() {
        assert_eq!(clamp_display(999), "999");
    }

    #[test]
    fn clamp_overflow_one() {
        assert_eq!(clamp_display(1000), "999+");
    }

    #[test]
    fn clamp_u32_max() {
        assert_eq!(clamp_display(u32::MAX), "999+");
    }
}
