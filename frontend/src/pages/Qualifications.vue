<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, putJson } from '../api'

const data = ref(null)
const snapshots = ref([])
const rejections = ref([])
const err = ref('')
const msg = ref('')

const shifts = computed(() => data.value?.班次 || [])
const zones = computed(() => data.value?.责任区 || [])
const cellMap = computed(() => {
  const m = {}
  for (const c of data.value?.矩阵 || []) m[`${c.shift_id}-${c.zone_id}`] = c
  return m
})

function cell(shiftId, zoneId) {
  return cellMap.value[`${shiftId}-${zoneId}`]
}

async function refresh() {
  data.value = await getJson('/api/qualifications')
  snapshots.value = await getJson('/api/qualification-snapshots')
  rejections.value = await getJson('/api/handover-rejections')
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function toggle(shift, zone) {
  msg.value = ''
  err.value = ''
  const cur = cell(shift.id, zone.id)
  try {
    await putJson('/api/qualifications', {
      shift_id: shift.id,
      zone_id: zone.id,
      qualified: !cur?.qualified,
    })
    msg.value = `${shift.name} × ${zone.name} 资格已改为 ${cur?.qualified ? '否' : '可'}`
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function sideName(side) {
  return side === 'from' ? '交班方' : '接班方'
}
</script>

<template>
  <div>
    <h1>资格矩阵</h1>
    <p class="lead">
      班次 × 责任区资格是唯一判据：清单内每个责任区，交班方与接班方双方都必须为「可」，
      否则整单拒绝发起。点击单元格即可维护「可 / 否」。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <p v-if="msg" class="msg ok">{{ msg }}</p>

    <div class="panel">
      <h2>班次 × 责任区资格矩阵</h2>
      <table class="matrix">
        <thead>
          <tr>
            <th>班次 \ 责任区</th>
            <th v-for="z in zones" :key="z.id">{{ z.name }}<span class="sub">{{ z.code }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in shifts" :key="s.id">
            <td class="shift-col">{{ s.name }}<span class="sub">{{ s.keeper }}</span></td>
            <td v-for="z in zones" :key="`${s.id}-${z.id}`">
              <button
                class="cell"
                :class="{ yes: cell(s.id, z.id)?.qualified }"
                @click="toggle(s, z)"
              >{{ cell(s.id, z.id)?.qualified ? '可' : '否' }}</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="hint">缺行按「否」处理；种子已补齐全部班次 × 责任区，矩阵可完整维护。</p>
    </div>

    <div class="panel">
      <h2>交班单资格快照（发起时固化，不可改写）</h2>
      <p class="hint">完成阶段只按此快照解释：当前矩阵变严不拒单，变宽也不放过发起时不合格的单。</p>
      <table>
        <thead>
          <tr><th>交班单</th><th>双方</th><th>班次</th><th>责任区</th><th>资格</th><th>固化时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="s in snapshots" :key="`${s.handover_id}-${s.shift_id}-${s.zone_id}`">
            <td>#{{ s.handover_id }}</td>
            <td>{{ sideName(s.side) }}</td>
            <td>{{ s.shift_name }}</td>
            <td>{{ s.zone_name }} <span class="sub">{{ s.zone_code }}</span></td>
            <td><span class="tag" :class="{ on: s.qualified }">{{ s.资格 }}</span></td>
            <td>{{ s.captured_at }}</td>
          </tr>
          <tr v-if="!snapshots.length"><td colspan="6" class="hint">暂无快照</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>发起失败原因（与矩阵、快照同源留档）</h2>
      <table>
        <thead>
          <tr><th>#</th><th>交班方</th><th>接班方</th><th>阶段</th><th>原因</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rejections" :key="r.id">
            <td>#{{ r.id }}</td>
            <td>{{ r.from_shift_id ?? '—' }}</td>
            <td>{{ r.to_shift_id ?? '—' }}</td>
            <td>{{ r.detail?.阶段 || '—' }}</td>
            <td class="reason">{{ r.reason }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
          <tr v-if="!rejections.length"><td colspan="6" class="hint">暂无拒绝记录</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
