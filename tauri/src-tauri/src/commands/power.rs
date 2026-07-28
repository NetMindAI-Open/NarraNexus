//! @file_name: power.rs
//! @description: Tauri commands for "Locked Use" — keep the computer awake
//! while background automations run.
//!
//! Contract with frontend (`frontend/src/stores/powerStore.ts`):
//! - `set_prevent_sleep(enabled)` asserts / releases the OS-level no-sleep
//!   state and returns the confirmed state. Non-macOS builds return Err so
//!   the frontend toggle stays off instead of lying.
//! - `get_prevent_sleep()` reports whether an assertion is currently held.
//!
//! macOS implementation: spawn `caffeinate -dims -w <our pid>`. The `-w`
//! flag ties the assertion to this process's lifetime, so a crash or quit
//! can never leave an orphan keeping the machine awake forever; toggling
//! off simply kills the child early.

use std::sync::Mutex;

/// Holds the caffeinate child while the assertion is active. Managed in
/// `lib.rs` via `.manage(PreventSleepState::default())`.
#[derive(Default)]
pub struct PreventSleepState(pub Mutex<Option<std::process::Child>>);

#[tauri::command]
pub fn set_prevent_sleep(
    enabled: bool,
    state: tauri::State<PreventSleepState>,
) -> Result<bool, String> {
    let mut guard = state
        .0
        .lock()
        .map_err(|e| format!("prevent-sleep state poisoned: {e}"))?;

    if !enabled {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
            log::info!("Locked Use released (caffeinate stopped)");
        }
        return Ok(false);
    }

    if guard.is_some() {
        return Ok(true); // already asserted — idempotent
    }

    #[cfg(target_os = "macos")]
    {
        let child = std::process::Command::new("caffeinate")
            .args(["-dims", "-w", &std::process::id().to_string()])
            .spawn()
            .map_err(|e| format!("failed to start caffeinate: {e}"))?;
        log::info!("Locked Use asserted (caffeinate pid {})", child.id());
        *guard = Some(child);
        Ok(true)
    }

    #[cfg(not(target_os = "macos"))]
    {
        Err("Locked Use is only supported on macOS".to_string())
    }
}

#[tauri::command]
pub fn get_prevent_sleep(state: tauri::State<PreventSleepState>) -> bool {
    state
        .0
        .lock()
        .map(|guard| guard.is_some())
        .unwrap_or(false)
}
