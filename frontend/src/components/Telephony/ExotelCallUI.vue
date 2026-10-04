<template>
  <div class="flex items-center">
    <button
      v-if="softphoneRequired && !showSmallCallPopup"
      type="button"
      class="ml-2"
      :title="statusBadgeTitle"
      @click="onStatusBadgeClick"
    >
      <Badge
        variant="subtle"
        :theme="statusBadge.theme"
        :label="__(statusBadge.label)"
      />
    </button>
    <div
      v-show="showSmallCallPopup"
      class="ml-2 flex cursor-pointer select-none items-center justify-between gap-1 rounded-full bg-surface-gray-7 px-2 py-[7px] text-base text-ink-gray-2"
      @click="toggleCallPopup"
    >
      <div
        class="flex justify-center items-center size-5 rounded-full bg-surface-gray-6 shrink-0 mr-1"
      >
        <Avatar
          v-if="contact?.image"
          :image="contact.image"
          :label="contact.full_name"
          class="!size-5"
        />
        <AvatarIcon v-else class="size-3" />
      </div>
      <span>{{ contact?.full_name ?? contact?.mobile_no }}</span>
      <span>·</span>
      <div v-if="callStatus == 'In progress'">
        {{ counterUp?.updatedTime }}
      </div>
      <div
        v-else-if="callStatus == 'Call ended' || callStatus == 'No answer'"
        class="blink"
        :class="{
          'text-red-700':
            callStatus == 'Call ended' || callStatus == 'No answer',
        }"
      >
        <span>{{ __(callStatus) }}</span>
        <span v-if="callStatus == 'Call ended'">
          <span> · </span>
          <span>{{ callDuration }}</span>
        </span>
      </div>
      <div v-else>{{ __(callStatus) }}</div>
    </div>
    <div
      v-show="showCallPopup"
      class="fixed z-20 w-[280px] min-h-44 flex gap-2 flex-col rounded-lg bg-surface-gray-7 p-4 pt-2.5 text-ink-gray-2 shadow-2xl"
      :style="style"
      @click.stop
    >
      <div
        ref="callPopupHeader"
        class="header flex items-center justify-between gap-1 text-base cursor-move select-none"
      >
        <div class="flex gap-2 items-center truncate">
          <div
            v-if="showNote || showTask"
            class="flex items-center gap-3 truncate"
          >
            <Avatar
              v-if="contact?.image"
              :image="contact.image"
              :label="contact.full_name"
              class="!size-7 shrink-0"
            />
            <div
              v-else
              class="flex justify-center items-center size-7 rounded-full bg-surface-gray-6 shrink-0"
            >
              <AvatarIcon class="size-3" />
            </div>
            <div
              class="flex flex-col gap-1 text-base leading-4 overflow-hidden"
            >
              <div class="font-medium truncate">
                {{ contact?.full_name ?? contact?.mobile_no }}
              </div>
              <div class="text-ink-gray-6">
                <div v-if="callStatus == 'In progress'">
                  <span>{{ contact?.mobile_no }}</span>
                  <span> · </span>
                  <span>{{ counterUp?.updatedTime }}</span>
                </div>
                <div
                  v-else-if="
                    callStatus == 'Call ended' || callStatus == 'No answer'
                  "
                  class="blink"
                  :class="{
                    'text-red-700':
                      callStatus == 'Call ended' || callStatus == 'No answer',
                  }"
                >
                  <span>{{ __(callStatus) }}</span>
                  <span v-if="callStatus == 'Call ended'">
                    <span> · </span>
                    <span>{{ callDuration }}</span>
                  </span>
                </div>
                <div v-else>{{ __(callStatus) }}</div>
              </div>
            </div>
          </div>
          <div v-else>
            <div v-if="callStatus == 'In progress'">
              {{ counterUp?.updatedTime }}
            </div>
            <div
              v-else-if="
                callStatus == 'Call ended' || callStatus == 'No answer'
              "
              class="blink"
              :class="{
                'text-red-700':
                  callStatus == 'Call ended' || callStatus == 'No answer',
              }"
            >
              <span>{{ __(callStatus) }}</span>
              <span v-if="callStatus == 'Call ended'">
                <span> · </span>
                <span>{{ callDuration }}</span>
              </span>
            </div>
            <div v-else>{{ __(callStatus) }}</div>
          </div>
        </div>

        <div class="flex">
          <Button
            class="bg-surface-gray-7 text-ink-white hover:bg-surface-gray-6 shrink-0 cursor-pointer"
            :tooltip="__('Minimize')"
            :icon="MinimizeIcon"
            size="md"
            @click="toggleCallPopup"
          />
          <Button
            class="bg-surface-gray-7 text-ink-white hover:bg-surface-gray-6 shrink-0"
            icon="x"
            size="md"
            :disabled="!canClose"
            :tooltip="closeTooltip"
            @click="attemptCloseCallPopup"
          />
        </div>
      </div>
      <div class="body flex-1">
        <div v-if="showNote">
          <TextEditor
            ref="content"
            variant="ghost"
            editor-class="prose-sm h-[290px] text-ink-white overflow-auto mt-1"
            :bubbleMenu="true"
            :content="note.content"
            :placeholder="__('Take a note...')"
            @change="(val) => (note.content = val)"
          />
        </div>
        <TaskPanel v-else-if="showTask" ref="taskRef" :task="task" />
        <div v-else class="flex items-center gap-3">
          <Avatar
            v-if="contact?.image"
            :image="contact.image"
            :label="contact.full_name"
            class="!size-8"
          />
          <div
            v-else
            class="flex justify-center items-center size-8 rounded-full bg-surface-gray-6"
          >
            <AvatarIcon class="size-4" />
          </div>
          <div v-if="contact?.full_name" class="flex flex-col gap-1">
            <div class="text-lg font-medium leading-5">
              {{ contact.full_name }}
            </div>
            <div class="text-base text-ink-gray-6 leading-4">
              {{ contact.mobile_no }}
            </div>
          </div>
          <div v-else class="text-lg font-medium leading-5">
            {{ contact.mobile_no }}
          </div>
        </div>
        <div
          v-if="
            dispositionRequired && (dispositionEligible || dispositionLocked)
          "
          class="mt-3"
        >
          <div class="text-sm text-ink-gray-5 mb-1">
            {{ __('Disposition') }}
            <span class="text-red-500">*</span>
          </div>
          <div
            v-if="dispositionLocked"
            class="w-full bg-surface-gray-6 text-ink-white px-3 py-1.5 rounded text-base"
          >
            {{ disposition || __('No Answer / Not Reachable') }}
          </div>
          <Dropdown v-else :options="dispositionDropdownOptions">
            <Button
              :label="disposition || __('Select a disposition...')"
              icon-right="chevron-down"
              class="!w-full !justify-between bg-surface-gray-6 text-ink-white hover:bg-surface-gray-5"
            />
          </Dropdown>
        </div>
        <div
          v-else-if="dispositionRequired && !dispositionEligible"
          class="mt-3 text-sm text-ink-gray-5"
        >
          {{ __('Disposition not required — lead is past C0.') }}
        </div>
        <div
          v-if="dispositionRequired && dispositionEligible && callbackRequired"
          class="mt-3"
        >
          <div class="text-sm text-ink-gray-5 mb-1">
            {{ __('Scheduled Callback At') }}
            <span class="text-red-500">*</span>
          </div>
          <input
            v-model="scheduledCallbackAt"
            type="datetime-local"
            :min="callbackMin"
            class="w-full bg-surface-gray-6 text-ink-white px-3 py-1.5 rounded text-base focus:outline-none [color-scheme:dark]"
          />
        </div>
        <div
          v-if="dispositionRequired && routingRequired"
          class="mt-3 flex flex-col gap-2"
        >
          <div>
            <div class="text-sm text-ink-gray-5 mb-1">
              {{ __('Routing Reason') }}
              <span class="text-red-500">*</span>
            </div>
            <select
              v-model="fabricatorRoutingReason"
              class="w-full bg-surface-gray-6 text-ink-white px-3 py-1.5 rounded text-base focus:outline-none"
            >
              <option value="">{{ __('Select a reason...') }}</option>
              <option v-for="r in routingReasonOptions" :key="r" :value="r">
                {{ r }}
              </option>
            </select>
          </div>
          <div>
            <div class="text-sm text-ink-gray-5 mb-1">
              {{ __('Partner Fabricator Name') }}
              <span class="text-red-500">*</span>
            </div>
            <input
              v-model="partnerFabricatorName"
              type="text"
              class="w-full bg-surface-gray-6 text-ink-white px-3 py-1.5 rounded text-base focus:outline-none"
            />
          </div>
          <div v-if="fabricatorRoutingReason === 'Other'">
            <div class="text-sm text-ink-gray-5 mb-1">
              {{ __('Routing Notes') }}
              <span class="text-red-500">*</span>
            </div>
            <textarea
              v-model="fabricatorRoutingNotes"
              class="w-full bg-surface-gray-6 text-ink-white px-3 py-1.5 rounded text-base focus:outline-none"
            />
          </div>
        </div>
      </div>
      <div
        v-if="softphoneSessionActive"
        class="flex items-center justify-center gap-2"
      >
        <template v-if="softphoneIncoming">
          <Button
            variant="solid"
            theme="green"
            :label="__('Accept')"
            icon="phone"
            :disabled="!softphoneIncomingReady"
            @click="acceptSoftphoneIncomingCall"
          />
          <Button
            variant="solid"
            theme="red"
            :label="__('Reject')"
            icon="phone-off"
            @click="rejectSoftphoneCall"
          />
        </template>
        <template v-else>
          <Button
            class="bg-surface-gray-6 text-ink-white hover:bg-surface-gray-5"
            :tooltip="softphoneMuted ? __('Unmute') : __('Mute')"
            :icon="softphoneMuted ? 'mic-off' : 'mic'"
            :disabled="!softphoneCallAvailable"
            @click="toggleSoftphoneMute"
          />
          <Button
            class="bg-surface-gray-6 text-ink-white hover:bg-surface-gray-5"
            :tooltip="softphoneHeld ? __('Resume') : __('Hold')"
            :icon="softphoneHeld ? 'play' : 'pause'"
            :disabled="!softphoneCallAvailable"
            @click="toggleSoftphoneHold"
          />
          <Button
            variant="solid"
            theme="red"
            :tooltip="__('Hang Up')"
            icon="phone-off"
            @click="hangupSoftphoneCall"
          />
        </template>
      </div>
      <div class="footer flex justify-between gap-2">
        <div class="flex gap-2">
          <Button
            class="bg-surface-gray-6 text-ink-white hover:bg-surface-gray-5"
            :tooltip="__('Add a Note')"
            size="md"
            :icon="NoteIcon"
            @click="showNoteWindow"
          />
          <Button
            class="bg-surface-gray-6 text-ink-white hover:bg-surface-gray-5"
            size="md"
            :tooltip="__('Add a Task')"
            :icon="TaskIcon"
            @click="showTaskWindow"
          />
          <Button
            v-if="contact.deal || contact.lead"
            class="bg-surface-gray-6 text-ink-white hover:bg-surface-gray-5"
            size="md"
            :iconRight="ArrowUpRightIcon"
            :label="contact.deal ? __('Deal') : __('Lead')"
            @click="openDealOrLead"
          />
        </div>

        <Button
          v-if="(note.name || task.name) && dirty"
          class="bg-surface-white !text-ink-gray-9 hover:!bg-surface-gray-3"
          variant="solid"
          :label="__('Update')"
          size="md"
          @click="update"
        />
        <Button
          v-else-if="
            ((note?.content && note.content != '<p></p>') || task.title) &&
            !note.name &&
            !task.name
          "
          class="bg-surface-white !text-ink-gray-9 hover:!bg-surface-gray-3"
          variant="solid"
          :label="__('Save')"
          size="md"
          @click="save"
        />
      </div>
    </div>
    <CountUpTimer ref="counterUp" />
  </div>
</template>
<script setup>
import ArrowUpRightIcon from '@/components/Icons/ArrowUpRightIcon.vue'
import AvatarIcon from '@/components/Icons/AvatarIcon.vue'
import MinimizeIcon from '@/components/Icons/MinimizeIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import TaskPanel from '@/components/Telephony/TaskPanel.vue'
import CountUpTimer from '@/components/CountUpTimer.vue'
import {
  persistNoteOnCallLog,
  persistTaskOnCallLog,
  TASK_DEFAULTS,
} from '@/composables/useCallLogActions.js'
import { globalStore } from '@/stores/global'
import { sessionStore } from '@/stores/session'
import {
  acceptExotelSoftphoneCall,
  hangupExotelSoftphoneCall,
  initializeExotelSoftphone,
  isExotelSoftphoneRegistered,
  subscribeToExotelSoftphone,
  toggleExotelSoftphoneHold,
  toggleExotelSoftphoneMute,
  unregisterExotelSoftphone,
} from '@/utils/exotelSoftphone'
import {
  chooseOutboundRoute,
  createOutboundDialTracker,
  createReconnectPolicy,
  extractExotelCallSid,
  softphoneStatusBadge,
  softphoneTerminalLabel,
} from '@/utils/exotelSoftphoneCall'
import { claimSoftphoneTab } from '@/utils/exotelSoftphoneTab'
import { createRingtone } from '@/utils/ringtone'
import { useDraggable, useWindowSize } from '@vueuse/core'
import {
  TextEditor,
  Avatar,
  Badge,
  Button,
  Dropdown,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { computed, ref, onBeforeUnmount, onMounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'

const { $socket } = globalStore()

const callPopupHeader = ref(null)
const showCallPopup = ref(false)
let showSmallCallPopup = ref(false)

function toggleCallPopup() {
  showCallPopup.value = !showCallPopup.value
  showSmallCallPopup.value = !showSmallCallPopup.value
}

const { width, height } = useWindowSize()

let { style } = useDraggable(callPopupHeader, {
  initialValue: { x: width.value - 350, y: height.value - 250 },
  preventDefault: true,
})

const callStatus = ref('')
const phoneNumber = ref('')
const callData = ref(null)
const counterUp = ref(null)
// True for agents opted in to the browser softphone: their calls never fall back to the mobile.
// null until get_softphone_config answers, so an early click can't fall back to the mobile.
const softphoneRequired = ref(null)
const softphoneRegistrationState = ref('not configured')
const softphoneSessionActive = ref(false)
const softphoneIncoming = ref(false)
const softphoneIncomingReady = ref(false)
const ringtone = createRingtone()
watch(softphoneIncoming, (incoming) =>
  incoming ? ringtone.start() : ringtone.stop(),
)
const softphoneCallAvailable = ref(false)
const softphoneMuted = ref(false)
const softphoneHeld = ref(false)
let softphoneConnected = false
let softphoneSetupPromise = null
let softphoneUnsubscribe = null
const outboundDial = createOutboundDialTracker()
let softphoneWasReady = false

const contact = ref({
  full_name: '',
  image: '',
  mobile_no: '',
})

const getContact = createResource({
  url: 'crm.integrations.api.get_contact_by_phone_number',
  makeParams() {
    return {
      phone_number: phoneNumber.value,
    }
  },
  onSuccess(data) {
    contact.value = data
  },
})

watch(
  phoneNumber,
  (value) => {
    if (!value) return
    getContact.fetch()
  },
  { immediate: true },
)

const dirty = ref(false)

const note = ref({
  name: '',
  content: '',
})

const showNote = ref(false)

function showNoteWindow() {
  showNote.value = !showNote.value
  if (!showTask.value) {
    updateWindowHeight(showNote.value)
  }
  if (showNote.value) {
    showTask.value = false
  }
}

async function createUpdateNote() {
  const _note = await persistNoteOnCallLog(callData.value.CallSid, note.value)
  note.value['name'] = _note.name
  await nextTick()
  dirty.value = false
}

const task = ref({
  name: '',
  title: '',
  description: '',
  assigned_to: '',
  due_date: '',
  ...TASK_DEFAULTS,
})

const showTask = ref(false)

function showTaskWindow() {
  showTask.value = !showTask.value
  if (!showNote.value) {
    updateWindowHeight(showTask.value)
  }
  if (showTask.value) {
    showNote.value = false
  }
}

async function createUpdateTask() {
  const _task = await persistTaskOnCallLog(callData.value.CallSid, task.value)
  task.value['name'] = _task.name
  await nextTick()
  dirty.value = false
}

watch([note, task], () => (dirty.value = true), { deep: true })

const dispositions = ref([])
const disposition = ref(null)
const scheduledCallbackAt = ref(null)
const fabricatorRoutingReason = ref(null)
const partnerFabricatorName = ref('')
const fabricatorRoutingNotes = ref('')
const isSavingDisposition = ref(false)

const lastSocketAt = ref(Date.now())
let staleCheckTimer = null
const PRE_ANSWER_STALE_MS = 90 * 1000
const ACTIVE_STALE_MS = 60 * 1000
const IN_PROGRESS_STALE_MS = 30 * 60 * 1000
const STALE_CHECK_INTERVAL_MS = 10 * 1000
const PRE_ANSWER_STATUSES = ['Calling...', 'Ringing...', 'Incoming call']
const DISPOSITION_SAVE_MAX_ATTEMPTS = 3
const DISPOSITION_SAVE_BACKOFF_MS = [1000, 2000, 4000]

const routingReasonOptions = [
  'Price Mismatch',
  'GST Issue',
  'Serviceability',
  'Other',
]

const dispositionsResource = createResource({
  url: 'frappe.client.get_list',
  params: {
    doctype: 'CRM Call Disposition',
    filters: { enabled: 1 },
    fields: [
      'name',
      'label',
      'color',
      'position',
      'next_status',
      'requires_callback_datetime',
      'requires_routing_reason',
    ],
    order_by: 'position asc',
    limit_page_length: 50,
  },
  auto: false,
  onSuccess(data) {
    dispositions.value = data || []
  },
})

const callbackRequired = computed(() => {
  if (!disposition.value) return false
  const d = dispositions.value.find((x) => x.name === disposition.value)
  return !!d?.requires_callback_datetime
})

const routingRequired = computed(() => {
  if (!disposition.value) return false
  const d = dispositions.value.find((x) => x.name === disposition.value)
  return !!d?.requires_routing_reason
})

function pad(n) {
  return String(n).padStart(2, '0')
}

const callbackMin = computed(() => {
  const now = new Date()
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`
})

watch(disposition, () => {
  if (!callbackRequired.value) {
    scheduledCallbackAt.value = null
  }
  if (!routingRequired.value) {
    fabricatorRoutingReason.value = null
    partnerFabricatorName.value = ''
    fabricatorRoutingNotes.value = ''
  }
})

const dispositionLocked = computed(() => callStatus.value === 'No answer')

// Outcome dispositions are only meaningful on leads in the disposition-eligible set
// (C0 OR engagement Cold-Unresponsive/Reactivated). Mirrors the backend gate in
// crm/api/call_log.py:register_no_answer and the disposition Before-Save server
// script. For deals or unmatched numbers (no contact.lead), the gate doesn't apply.
const dispositionEligible = computed(() => {
  if (!contact.value?.lead) return true
  return (
    contact.value.status === 'C0' ||
    ['Cold-Unresponsive', 'Reactivated'].includes(contact.value.lead_status)
  )
})

const dispositionDropdownOptions = computed(() =>
  dispositions.value
    .filter((d) => {
      // 'No Answer / Not Reachable' contradicts a Call ended (completed) call.
      if (
        callStatus.value === 'Call ended' &&
        d.name === 'No Answer / Not Reachable'
      )
        return false
      return true
    })
    .map((d) => ({
      label: d.label || d.name,
      onClick: () => (disposition.value = d.name),
    })),
)

const callTerminated = computed(
  () => callStatus.value === 'Call ended' || callStatus.value === 'No answer',
)

// Re-fetch lead state at call-end so dispositionEligible evaluates against
// current DB values, not the snapshot captured when the call started.
watch(callTerminated, (val) => {
  if (val && phoneNumber.value) getContact.fetch()
})

const callActive = computed(() =>
  ['Calling...', 'Ringing...', 'In progress', 'Incoming call'].includes(
    callStatus.value,
  ),
)

const POPUP_STATE_KEY = 'exotel_call_popup_state'

// State older than 2 h is considered stale and is not restored. This limits
// how long PII (phone number) survives in sessionStorage if the browser tab
// is left open after a call without the popup being closed.
const POPUP_STATE_TTL_MS = 2 * 60 * 60 * 1000

function persistPopupState() {
  if (!callData.value?.CallSid) {
    try {
      sessionStorage.removeItem(POPUP_STATE_KEY)
    } catch {
      /* storage unavailable */
    }
    return
  }
  try {
    sessionStorage.setItem(
      POPUP_STATE_KEY,
      JSON.stringify({
        savedAt: Date.now(),
        callData: callData.value,
        callStatus: callStatus.value,
        phoneNumber: phoneNumber.value,
        showCallPopup: showCallPopup.value,
        showSmallCallPopup: showSmallCallPopup.value,
        disposition: disposition.value,
        scheduledCallbackAt: scheduledCallbackAt.value,
        fabricatorRoutingReason: fabricatorRoutingReason.value,
        partnerFabricatorName: partnerFabricatorName.value,
        fabricatorRoutingNotes: fabricatorRoutingNotes.value,
        lastSocketAt: lastSocketAt.value,
      }),
    )
  } catch {
    /* storage unavailable */
  }
}

function restorePopupState() {
  try {
    const raw = sessionStorage.getItem(POPUP_STATE_KEY)
    if (!raw) return
    const s = JSON.parse(raw)
    if (!s?.callData?.CallSid) return
    // Discard stale state — protects PII after long idle periods.
    if (s.savedAt && Date.now() - s.savedAt > POPUP_STATE_TTL_MS) {
      sessionStorage.removeItem(POPUP_STATE_KEY)
      return
    }
    callData.value = s.callData
    callStatus.value = s.callStatus || ''
    phoneNumber.value = s.phoneNumber || ''
    showCallPopup.value = !!s.showCallPopup
    showSmallCallPopup.value = !!s.showSmallCallPopup
    disposition.value = s.disposition || null
    scheduledCallbackAt.value = s.scheduledCallbackAt || null
    fabricatorRoutingReason.value = s.fabricatorRoutingReason || null
    partnerFabricatorName.value = s.partnerFabricatorName || ''
    fabricatorRoutingNotes.value = s.fabricatorRoutingNotes || ''
    if (typeof s.lastSocketAt === 'number') lastSocketAt.value = s.lastSocketAt
  } catch {
    /* parse error — ignore */
  }
}

function clearPopupState() {
  try {
    sessionStorage.removeItem(POPUP_STATE_KEY)
  } catch {
    /* storage unavailable */
  }
}

watch(
  [
    callData,
    callStatus,
    phoneNumber,
    showCallPopup,
    showSmallCallPopup,
    disposition,
    scheduledCallbackAt,
    fabricatorRoutingReason,
    partnerFabricatorName,
    fabricatorRoutingNotes,
    lastSocketAt,
  ],
  persistPopupState,
  { deep: true },
)

const dispositionRequired = callTerminated

// UX-only guard: disable the close button when we know for sure the session
// user isn't the call handler. The backend Disposition Validation server script
// is the authoritative check — this just prevents an obvious mismatch from
// reaching the API.
const isCallHandler = computed(() => {
  if (!callData.value?.AgentEmail) return true // unknown — don't block
  const { user } = sessionStore()
  const sessionUser = typeof user === 'object' ? user.value : user
  return callData.value.AgentEmail === sessionUser
})

const canClose = computed(() => {
  if (isSavingDisposition.value) return false
  if (callActive.value) return false
  if (callTerminated.value && !isCallHandler.value) return false
  if (callTerminated.value) {
    if (!dispositionEligible.value) return true
    if (!disposition.value) return false
    if (callbackRequired.value && !scheduledCallbackAt.value) return false
    if (
      routingRequired.value &&
      (!fabricatorRoutingReason.value || !partnerFabricatorName.value)
    )
      return false
    if (
      routingRequired.value &&
      fabricatorRoutingReason.value === 'Other' &&
      !fabricatorRoutingNotes.value
    )
      return false
    return true
  }
  return true
})

const closeTooltip = computed(() => {
  if (isSavingDisposition.value) return __('Saving disposition...')
  if (callActive.value) return __('Wait for the call to end')
  if (callTerminated.value && !isCallHandler.value)
    return __('Only the agent who handled this call can save the disposition')
  if (callTerminated.value && dispositionEligible.value && !disposition.value)
    return __('Select a disposition to close')
  if (
    callTerminated.value &&
    callbackRequired.value &&
    !scheduledCallbackAt.value
  )
    return __('Set the scheduled callback datetime to close')
  if (
    callTerminated.value &&
    routingRequired.value &&
    (!fabricatorRoutingReason.value || !partnerFabricatorName.value)
  )
    return __('Fill in Routing Reason and Partner Fabricator Name to close')
  if (
    callTerminated.value &&
    routingRequired.value &&
    fabricatorRoutingReason.value === 'Other' &&
    !fabricatorRoutingNotes.value
  )
    return __('Routing Notes are required when reason is "Other"')
  return __('Close')
})

const STATUS_DEFAULT_DISPOSITION = {
  'No answer': 'No Answer / Not Reachable',
  'Call ended': 'Requested Callback',
}

watch(
  [callStatus, dispositions],
  () => {
    if (disposition.value || dispositions.value.length === 0) return
    const target = STATUS_DEFAULT_DISPOSITION[callStatus.value]
    if (!target) return
    const preselect = dispositions.value.find((d) => d.name === target)
    if (preselect) disposition.value = preselect.name
  },
  { immediate: true },
)

function updateWindowHeight(condition) {
  let callPopup = callPopupHeader.value.parentElement
  let top = parseInt(callPopup.style.top)
  let updatedTop

  updatedTop = condition ? top - 224 : top + 224

  if (updatedTop < 0) {
    updatedTop = 10
  }

  callPopup.style.top = updatedTop + 'px'
}

async function makeOutgoingCall(number, context) {
  const route = chooseOutboundRoute({
    softphoneRequired: softphoneRequired.value,
    registered: isExotelSoftphoneRegistered(),
    registrationState: softphoneRegistrationState.value,
  })
  if (route === 'click-to-call') makeClassicOutgoingCall(number, context)
  else if (route === 'softphone')
    await makeSoftphoneOutgoingCall(number, context)
  else showSoftphoneUnavailable(route)
}

function showSoftphoneUnavailable(route) {
  if (route === 'blocked-loading') {
    toast.info(
      __('Your browser phone is still connecting. Try again in a moment.'),
    )
    return
  }
  if (route === 'blocked-other-tab') {
    toast.info(__('Calls are running in another CRM tab.'), {
      duration: 10,
      action: { label: __('Use this tab'), onClick: takeOverSoftphoneTab },
    })
    return
  }
  if (route === 'blocked-browser') {
    toast.error(
      __(
        'Browser calling needs Chrome or Edge. Open the CRM in Chrome to call.',
      ),
    )
    return
  }
  toast.error(
    __('Browser softphone is not connected ({0}). The call was not placed.', [
      softphoneRegistrationState.value,
    ]),
    {
      duration: 10,
      action: { label: __('Reconnect'), onClick: reconnectSoftphone },
    },
  )
}

let reconnecting = false
let isSoftphoneTab = false
let releaseSoftphoneTab = null

function softphoneUser() {
  const { user } = sessionStore()
  return typeof user === 'object' ? user.value : user
}

function claimTab({ steal = false } = {}) {
  releaseSoftphoneTab?.()
  releaseSoftphoneTab = claimSoftphoneTab({
    user: softphoneUser(),
    steal,
    onAcquired: () => {
      isSoftphoneTab = true
      reconnectSoftphone({ silent: true })
    },
    onLost: () => {
      isSoftphoneTab = false
      unregisterExotelSoftphone()
      softphoneSetupPromise = null
      softphoneRegistrationState.value = 'other tab'
      claimTab()
    },
  })
}

function takeOverSoftphoneTab() {
  claimTab({ steal: true })
}

async function reconnectSoftphone({ silent = false } = {}) {
  if (reconnecting) return
  reconnecting = true
  try {
    unregisterExotelSoftphone()
    softphoneSetupPromise = null
    softphoneWasReady = false
    await setupSoftphone({ silent })
  } finally {
    reconnecting = false
  }
}

const statusBadge = computed(() =>
  softphoneStatusBadge(softphoneRegistrationState.value),
)
const statusBadgeTitle = computed(() => {
  const state = softphoneRegistrationState.value
  if (state === 'other tab')
    return __('Calls run in another CRM tab. Click to use this tab instead.')
  if (['registered', 'unsupported browser'].includes(state))
    return __('Browser softphone: {0}', [state])
  return __('Browser softphone: {0}. Click to reconnect.', [state])
})

function onStatusBadgeClick() {
  if (softphoneRegistrationState.value === 'other tab') {
    takeOverSoftphoneTab()
    return
  }
  if (
    ['registered', 'unsupported browser'].includes(
      softphoneRegistrationState.value,
    )
  )
    return
  reconnectSoftphone()
}

const reconnectPolicy = createReconnectPolicy()
const RECONNECT_CHECK_INTERVAL_MS = 5 * 1000
let reconnectTimer = null

watch(softphoneRegistrationState, (state) => reconnectPolicy.onState(state))

function checkSoftphoneReconnect() {
  if (
    !softphoneRequired.value ||
    softphoneRegistrationState.value === 'unsupported browser' ||
    !isSoftphoneTab ||
    reconnecting
  )
    return
  if (
    !reconnectPolicy.shouldReconnect({
      inCall: softphoneSessionActive.value,
      online: navigator.onLine,
    })
  )
    return
  reconnectPolicy.attempted()
  console.info(
    '[exotel-softphone] auto-reconnect',
    softphoneRegistrationState.value,
  )
  reconnectSoftphone({ silent: true })
}

function onBrowserOnline() {
  reconnectPolicy.reconnectSoon()
  checkSoftphoneReconnect()
}

function startReconnectWatchdog() {
  if (reconnectTimer) return
  reconnectTimer = setInterval(
    checkSoftphoneReconnect,
    RECONNECT_CHECK_INTERVAL_MS,
  )
  window.addEventListener('online', onBrowserOnline)
}

function stopReconnectWatchdog() {
  clearInterval(reconnectTimer)
  reconnectTimer = null
  window.removeEventListener('online', onBrowserOnline)
}

function makeClassicOutgoingCall(number, context) {
  phoneNumber.value = number

  const params = { to_number: phoneNumber.value }
  if (context?.reference_doctype && context?.reference_docname) {
    params.reference_doctype = context.reference_doctype
    params.reference_docname = context.reference_docname
  }

  createResource({
    url: 'crm.integrations.exotel.handler.make_a_call',
    params,
    auto: true,
    onSuccess(callDetails) {
      callData.value = callDetails
      console.log(callDetails)

      callStatus.value = 'Calling...'
      showCallPopup.value = true
      showSmallCallPopup.value = false
      lastSocketAt.value = Date.now()
    },
    onError(err) {
      toast.error(err.messages?.[0] || err.message || __('Exotel call failed'))
    },
  })
}

// Called both on mount and by CallUI once integrations load; must only run once.
let setupDone = false

function setup() {
  if (setupDone) return
  setupDone = true
  dispositionsResource.fetch()
  restorePopupState()
  $socket.off('exotel_call')
  $socket.on('exotel_call', (data) => {
    lastSocketAt.value = Date.now()
    callData.value = data
    console.log('[exotel] socket event received', data)

    callStatus.value = updateStatus(data)
    const { user } = sessionStore()
    const sessionUser = typeof user === 'object' ? user.value : user

    if (!showCallPopup.value && !showSmallCallPopup.value) {
      if (callTerminated.value) return
      if (data.AgentEmail && data.AgentEmail === sessionUser) {
        // Incoming call
        phoneNumber.value = data.CallFrom || data.From
        showCallPopup.value = true
      } else {
        // Outgoing call
        phoneNumber.value = data.To
      }
    }
  })
  startStaleCheck()
  setupSoftphone()
  startReconnectWatchdog()
}

onMounted(setup)

// Safari connects Exotel calls without audio in either direction.
function isSafari() {
  const ua = navigator.userAgent
  return (
    /safari/i.test(ua) && !/chrome|chromium|crios|fxios|edg|android/i.test(ua)
  )
}

function setupSoftphone({ silent = false } = {}) {
  if (softphoneSetupPromise) return softphoneSetupPromise
  if (!softphoneUnsubscribe) {
    softphoneUnsubscribe = subscribeToExotelSoftphone({
      onCallEvent: handleSoftphoneCallEvent,
      onRegistration: handleSoftphoneRegistration,
    })
  }

  softphoneSetupPromise = (async () => {
    try {
      const config = await call(
        'crm.integrations.exotel.handler.get_softphone_config',
      )
      softphoneRequired.value = Boolean(config.enabled)
      if (!softphoneRequired.value) return
      if (isSafari()) {
        softphoneRegistrationState.value = 'unsupported browser'
        toast.error(
          __(
            'Browser calling needs Chrome or Edge. Open the CRM in Chrome to make and receive calls.',
          ),
        )
        return
      }
      if (!isSoftphoneTab) {
        softphoneRegistrationState.value = 'other tab'
        if (!releaseSoftphoneTab) claimTab()
        return
      }
      softphoneRegistrationState.value = 'initializing'
      await initializeExotelSoftphone(config)
    } catch (error) {
      // get_softphone_config only throws for agents who are opted in but not set up correctly.
      softphoneRequired.value = true
      softphoneRegistrationState.value = 'failed'
      console.error('[exotel-softphone] initialization failed', error)
      // Auto-reconnect retries in the background; the header badge shows the state.
      if (silent) return
      toast.error(
        error.messages?.[0] ||
          error.message ||
          __('Browser softphone could not start.'),
        {
          duration: 10,
          action: { label: __('Reconnect'), onClick: reconnectSoftphone },
        },
      )
    }
  })()
  return softphoneSetupPromise
}

function handleSoftphoneRegistration(state) {
  softphoneRegistrationState.value = state || 'unknown'
  console.info('[exotel-softphone] registration', state)
  if (state === 'registered' && !softphoneWasReady) {
    softphoneWasReady = true
    toast.success(__('Exotel browser softphone is ready'))
  } else if (state === 'unregistered' || state === 'failed') {
    softphoneWasReady = false
  }
}

async function makeSoftphoneOutgoingCall(number, context) {
  phoneNumber.value = number
  callStatus.value = 'Calling...'
  showCallPopup.value = true
  showSmallCallPopup.value = false
  softphoneSessionActive.value = true
  softphoneIncoming.value = false
  softphoneIncomingReady.value = false
  softphoneCallAvailable.value = false
  softphoneConnected = false
  softphoneMuted.value = false
  softphoneHeld.value = false
  lastSocketAt.value = Date.now()

  outboundDial.start()
  try {
    const response = await call(
      'crm.integrations.exotel.handler.make_softphone_call',
      {
        phone_number: number,
        reference_doctype: context?.reference_doctype || null,
        reference_docname: context?.reference_docname || null,
      },
    )
    const callSid = extractExotelCallSid(response)
    if (!callSid)
      throw new Error(__('Exotel dial response did not include CallSid'))

    callData.value = {
      CallSid: callSid,
      AgentEmail: sessionStore().user,
      Direction: 'outbound-dial',
      To: number,
    }
    const next = outboundDial.dialSucceeded(callSid)
    if (next.action === 'accept') {
      softphoneCallAvailable.value = true
      acceptPendingOutboundCall()
    } else if (next.action === 'reject') {
      hangupExotelSoftphoneCall()
    }
  } catch (error) {
    const next = outboundDial.dialFailed({
      outcomeUnknown: error.exc_type === 'ExotelDialOutcomeUnknown',
    })
    if (next.action === 'reject') hangupExotelSoftphoneCall()
    resetSoftphoneSession()
    closeCallPopup()
    toast.error(
      error.messages?.[0] ||
        error.message ||
        __('Could not start browser call. No mobile fallback was placed.'),
    )
    if (next.action === 'inbound')
      prepareSoftphoneIncomingCall(next.callSid, next.details)
  }
}

function handleSoftphoneCallEvent(eventType, details = {}) {
  const callSid = extractExotelCallSid(details)
  lastSocketAt.value = Date.now()

  if (eventType === 'incoming') {
    if (!callSid) {
      hangupExotelSoftphoneCall()
      toast.error(__('Rejected softphone call without a verifiable CallSid.'))
      return
    }
    const next = outboundDial.onIncoming(callSid, details)
    if (next.action === 'accept') {
      softphoneCallAvailable.value = true
      acceptPendingOutboundCall()
    } else if (next.action === 'reject') {
      hangupExotelSoftphoneCall()
    } else if (next.action === 'inbound') {
      prepareSoftphoneIncomingCall(callSid, details)
    }
    return
  }

  if (eventType === 'connected') {
    softphoneConnected = true
    softphoneIncoming.value = false
    softphoneIncomingReady.value = false
    softphoneCallAvailable.value = true
    softphoneSessionActive.value = true
    callStatus.value = 'In progress'
    counterUp.value?.start()
    return
  }

  if (eventType === 'callEnded') {
    if (outboundDial.pending) {
      outboundDial.onCallEnded(callSid)
      return
    }
    // A rejected INVITE ending must not tear down the call the agent is on.
    if (
      callSid &&
      callData.value?.CallSid &&
      callSid !== callData.value.CallSid
    )
      return
    counterUp.value?.stop()
    // A terminal webhook may already have set the real outcome; the agent leg connecting
    // doesn't mean the customer answered.
    if (!callTerminated.value)
      callStatus.value = softphoneConnected ? 'Call ended' : 'No answer'
    resetSoftphoneSession()
  }
}

function acceptPendingOutboundCall() {
  softphoneIncoming.value = false
  callStatus.value = 'Ringing...'
  acceptExotelSoftphoneCall()
}

async function prepareSoftphoneIncomingCall(callSid, details) {
  const number = details.callFromNumber || details.remoteId
  if (!number) {
    hangupExotelSoftphoneCall()
    toast.error(__('Rejected softphone call without a caller number.'))
    return
  }

  phoneNumber.value = number
  callData.value = {
    ...details,
    CallSid: callSid,
    AgentEmail: sessionStore().user,
    Direction: 'incoming',
    CallFrom: number,
  }
  callStatus.value = 'Incoming call'
  softphoneSessionActive.value = true
  softphoneIncoming.value = true
  softphoneIncomingReady.value = false
  softphoneCallAvailable.value = true
  softphoneConnected = false
  showCallPopup.value = true
  showSmallCallPopup.value = false

  try {
    const registered = await call(
      'crm.integrations.exotel.handler.register_softphone_call',
      {
        call_sid: callSid,
        phone_number: number,
        call_type: 'Incoming',
      },
    )
    // The server's caller number is verified against Exotel; the SIP caller ID may not be.
    if (registered?.CallFrom && registered.CallFrom !== number) {
      phoneNumber.value = registered.CallFrom
      callData.value = { ...callData.value, CallFrom: registered.CallFrom }
    }
    softphoneIncomingReady.value = true
  } catch (error) {
    hangupExotelSoftphoneCall()
    resetSoftphoneSession()
    closeCallPopup()
    toast.error(
      error.messages?.[0] ||
        error.message ||
        __('Could not register incoming browser call.'),
    )
  }
}

function acceptSoftphoneIncomingCall() {
  softphoneIncoming.value = false
  callStatus.value = 'Ringing...'
  acceptExotelSoftphoneCall()
}

function rejectSoftphoneCall() {
  hangupExotelSoftphoneCall()
  softphoneIncoming.value = false
}

function hangupSoftphoneCall() {
  hangupExotelSoftphoneCall()
}

function toggleSoftphoneMute() {
  toggleExotelSoftphoneMute()
  softphoneMuted.value = !softphoneMuted.value
}

function toggleSoftphoneHold() {
  toggleExotelSoftphoneHold()
  softphoneHeld.value = !softphoneHeld.value
}

function resetSoftphoneSession() {
  softphoneSessionActive.value = false
  softphoneIncoming.value = false
  softphoneIncomingReady.value = false
  softphoneCallAvailable.value = false
  softphoneMuted.value = false
  softphoneHeld.value = false
  softphoneConnected = false
  outboundDial.reset()
}

function startStaleCheck() {
  if (staleCheckTimer) return
  staleCheckTimer = setInterval(checkStale, STALE_CHECK_INTERVAL_MS)
}

function stopStaleCheck() {
  if (staleCheckTimer) {
    clearInterval(staleCheckTimer)
    staleCheckTimer = null
  }
}

function checkStale() {
  if (!showCallPopup.value && !showSmallCallPopup.value) return
  if (!callData.value?.CallSid) return
  // Once call is terminated, no more socket events are expected — stale
  // detection has no purpose and must not race against disposition save.
  if (callTerminated.value) return
  // The browser owns a connected softphone call; quiet webhooks must never hang it up.
  if (softphoneSessionActive.value && softphoneConnected) return
  // No intermediate socket events expected while call is live, so use a long
  // safety-net timeout rather than the short ACTIVE_STALE_MS — prevents
  // stale-close on normal calls while still closing the popup if the socket
  // breaks during an active call.
  const threshold =
    callStatus.value === 'In progress'
      ? IN_PROGRESS_STALE_MS
      : PRE_ANSWER_STATUSES.includes(callStatus.value)
        ? PRE_ANSWER_STALE_MS
        : ACTIVE_STALE_MS
  if (Date.now() - lastSocketAt.value <= threshold) return
  toast.info(
    __(
      'Call popup closed — no telephony update received. Add disposition via call log activity if needed.',
    ),
  )
  if (softphoneSessionActive.value) {
    hangupExotelSoftphoneCall()
    resetSoftphoneSession()
  }
  closeCallPopup()
}

onBeforeUnmount(() => {
  ringtone.stop()
  $socket.off('exotel_call')
  stopStaleCheck()
  stopReconnectWatchdog()
  releaseSoftphoneTab?.()
  releaseSoftphoneTab = null
  softphoneUnsubscribe?.()
  softphoneUnsubscribe = null
  unregisterExotelSoftphone()
  window.removeEventListener('beforeunload', unregisterExotelSoftphone)
})

window.addEventListener('beforeunload', unregisterExotelSoftphone)

const router = useRouter()

function openDealOrLead() {
  if (contact.value.deal) {
    router.push({
      name: 'Deal',
      params: { dealId: contact.value.deal },
    })
  } else if (contact.value.lead) {
    router.push({
      name: 'Lead',
      params: { leadId: contact.value.lead },
    })
  }
}

function closeCallPopup() {
  showCallPopup.value = false
  showSmallCallPopup.value = false
  note.value = {
    name: '',
    content: '',
  }
  task.value = {
    name: '',
    title: '',
    description: '',
    assigned_to: '',
    due_date: '',
    status: 'Backlog',
    priority: 'Low',
  }
  disposition.value = null
  scheduledCallbackAt.value = null
  fabricatorRoutingReason.value = null
  partnerFabricatorName.value = ''
  fabricatorRoutingNotes.value = ''
  callData.value = null
  callStatus.value = ''
  lastSocketAt.value = Date.now()
  clearPopupState()
}

async function attemptCloseCallPopup() {
  if (isSavingDisposition.value) return
  if (!dispositionRequired.value) {
    closeCallPopup()
    return
  }
  // Non-eligible leads (past C0, not Cold-Unresponsive/Reactivated) must not
  // have dispositions written — backend will reject them. Close without API call.
  if (!dispositionEligible.value) {
    closeCallPopup()
    return
  }
  if (!disposition.value) return
  if (callbackRequired.value && !scheduledCallbackAt.value) return
  if (!callData.value?.CallSid) {
    closeCallPopup()
    return
  }
  isSavingDisposition.value = true
  const payload = {
    call_sid: callData.value.CallSid,
    disposition: disposition.value,
    scheduled_callback_at: scheduledCallbackAt.value || null,
    fabricator_routing_reason: fabricatorRoutingReason.value || null,
    partner_fabricator_name: partnerFabricatorName.value || null,
    fabricator_routing_notes: fabricatorRoutingNotes.value || null,
  }
  let lastErr = null
  for (let attempt = 1; attempt <= DISPOSITION_SAVE_MAX_ATTEMPTS; attempt++) {
    try {
      await call('crm.integrations.api.add_disposition_to_call_log', payload)
      isSavingDisposition.value = false
      closeCallPopup()
      return
    } catch (err) {
      lastErr = err
      const msg =
        err?.messages?.[0] || err?.message || __('Failed to save disposition')
      if (attempt < DISPOSITION_SAVE_MAX_ATTEMPTS) {
        toast.error(
          `${msg} ${__('— retrying')} (${attempt}/${DISPOSITION_SAVE_MAX_ATTEMPTS - 1})`,
        )
        await new Promise((r) =>
          setTimeout(r, DISPOSITION_SAVE_BACKOFF_MS[attempt - 1] || 4000),
        )
      }
    }
  }
  isSavingDisposition.value = false
  toast.error(
    lastErr?.messages?.[0] ||
      lastErr?.message ||
      __(
        'Failed to save disposition after retries. Closing popup — add disposition via call log activity.',
      ),
  )
  closeCallPopup()
}

function save() {
  if (note.value.content) createUpdateNote()
  if (task.value.title) createUpdateTask()
}

function update() {
  if (note.value.content) createUpdateNote()
  if (task.value.title) createUpdateTask()
}

const callDuration = ref('00:00')

function updateStatus(data) {
  // outgoing call
  if (
    data.EventType == 'answered' &&
    ['outbound-api', 'outbound-dial'].includes(data.Direction) &&
    data.Status == 'in-progress' &&
    data['Legs[0][Status]'] == 'in-progress' &&
    data['Legs[1][Status]'] == ''
  ) {
    return 'Ringing...'
  } else if (
    data.EventType == 'answered' &&
    ['outbound-api', 'outbound-dial'].includes(data.Direction) &&
    data.Status == 'in-progress' &&
    data['Legs[1][Status]'] == 'in-progress'
  ) {
    counterUp.value.start()
    return 'In progress'
  } else if (
    data.EventType == 'terminal' &&
    ['outbound-api', 'outbound-dial'].includes(data.Direction) &&
    (data.Status == 'no-answer' || data.Status == 'busy') &&
    (data['Legs[1][Status]'] == 'no-answer' ||
      data['Legs[0][Status]'] == 'no-answer' ||
      data['Legs[1][Status]'] == 'busy' ||
      data['Legs[0][Status]'] == 'busy')
  ) {
    counterUp.value.stop()
    return 'No answer'
  } else if (
    data.EventType == 'terminal' &&
    ['outbound-api', 'outbound-dial'].includes(data.Direction) &&
    data.Status == 'completed'
  ) {
    counterUp.value.stop()
    callDuration.value = counterUp.value.getTime(
      parseInt(data['Legs[0][OnCallDuration]']) ||
        parseInt(data.DialCallDuration),
    )
    return 'Call ended'
  }

  // incoming call
  if (
    data.EventType == 'Dial' &&
    data.Direction == 'incoming' &&
    data.Status == 'busy'
  ) {
    phoneNumber.value = data.From || data.CallFrom
    return 'Incoming call'
  } else if (
    data.Direction == 'incoming' &&
    data.CallType == 'incomplete' &&
    data.DialCallStatus == 'no-answer'
  ) {
    return 'No answer'
  } else if (
    data.Direction == 'incoming' &&
    (data.CallType == 'completed' || data.CallType == 'client-hangup') &&
    (data.DialCallStatus == 'completed' || data.DialCallStatus == 'canceled')
  ) {
    callDuration.value = counterUp.value.getTime(
      parseInt(data['Legs[0][OnCallDuration]']) ||
        parseInt(data.DialCallDuration),
    )
    return 'Call ended'
  }

  const softphoneOutcome = softphoneTerminalLabel(data)
  if (softphoneOutcome) return softphoneOutcome

  // No branch matched — keep current status rather than returning undefined,
  // which would corrupt every computed that checks callStatus.value.
  return callStatus.value
}

defineExpose({ makeOutgoingCall, setup })
</script>
<style scoped>
@keyframes blink {
  0% {
    opacity: 1;
  }
  50% {
    opacity: 0;
  }
  100% {
    opacity: 1;
  }
}

.blink {
  animation: blink 1s ease-in-out 6;
}

:deep(.ProseMirror) {
  caret-color: var(--ink-white);
}
</style>
