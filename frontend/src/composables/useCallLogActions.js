/**
 * Shared call-log note and task helpers.
 *
 * Both ExotelCallUI (inline panel) and CallLogDetailModal (doctype modal) link
 * notes and tasks to a call log via the same two API endpoints. This composable
 * centralises:
 *
 *   - The endpoint names (avoid magic string duplication)
 *   - Default task values shared by both surfaces
 *   - `persistNoteOnCallLog` / `persistTaskOnCallLog` — thin async wrappers
 *     used by ExotelCallUI's inline save flow
 *   - `openNoteModal` / `openTaskModal` — modal-based flow used by
 *     CallLogDetailModal (delegates to useDoctypeModal)
 */
import { call } from 'frappe-ui'
import { useOnboarding, useTelemetry } from 'frappe-ui/frappe'
import { useDoctypeModal } from '@/composables/doctypeModal'

export const NOTE_DOCTYPE = 'FCRM Note'
export const TASK_DOCTYPE = 'CRM Task'
export const TASK_DEFAULTS = { status: 'Backlog', priority: 'Low' }

const ADD_NOTE_API = 'crm.integrations.api.add_note_to_call_log'
const ADD_TASK_API = 'crm.integrations.api.add_task_to_call_log'

/**
 * Core API helpers — framework-agnostic, used by ExotelCallUI's inline flow.
 *
 * @param {string} callSid  The Exotel CallSid (name of the CRM Call Log row).
 * @param {object} noteData Object with at least `{ content }`.
 * @returns {Promise<object>} The saved FCRM Note document.
 */
export async function persistNoteOnCallLog(callSid, noteData) {
  return call(ADD_NOTE_API, { call_sid: callSid, note: noteData })
}

/**
 * @param {string} callSid  The Exotel CallSid.
 * @param {object} taskData Task fields object.
 * @returns {Promise<object>} The saved CRM Task document.
 */
export async function persistTaskOnCallLog(callSid, taskData) {
  return call(ADD_TASK_API, { call_sid: callSid, task: taskData })
}

/**
 * Modal-based helpers for CallLogDetailModal.
 *
 * @param {object} callLogResource  A frappe-ui resource whose `.data.id` is the
 *                                  call SID and that exposes a `.reload()` method.
 * @returns {{ openNoteModal, openTaskModal }}
 */
export function useCallLogModalActions(callLogResource) {
  const { updateOnboardingStep } = useOnboarding('frappecrm')
  const { capture } = useTelemetry()
  const { showModal } = useDoctypeModal()

  function openNoteModal(noteName) {
    showModal({
      name: noteName,
      doctype: NOTE_DOCTYPE,
      title: 'Note',
      callbacks: {
        afterInsert: async (d) => {
          if (d.name) {
            await call(ADD_NOTE_API, {
              call_sid: callLogResource.value?.data?.id,
              note: d,
            })
            updateOnboardingStep('create_first_note')
            capture('note_created')
          }
          callLogResource.value?.reload?.()
        },
        afterUpdate: () => {
          capture('note_updated')
          callLogResource.value?.reload?.()
        },
      },
    })
  }

  function openTaskModal(taskName) {
    showModal({
      name: taskName,
      doctype: TASK_DOCTYPE,
      title: 'Task',
      defaults: TASK_DEFAULTS,
      callbacks: {
        afterInsert: async (d) => {
          if (d.name) {
            await call(ADD_TASK_API, {
              call_sid: callLogResource.value?.data?.id,
              task: d,
            })
            updateOnboardingStep('create_first_task')
            capture('task_created')
          }
          callLogResource.value?.reload?.()
        },
        afterUpdate: () => {
          capture('task_updated')
          callLogResource.value?.reload?.()
        },
      },
    })
  }

  return { openNoteModal, openTaskModal }
}
