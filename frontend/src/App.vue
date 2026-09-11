<template>
  <a-config-provider :locale="arcoLocale">
    <router-view></router-view>
  </a-config-provider>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'; // 导入 onMounted 生命周期钩子，用于组件初次挂载后执行初始化逻辑。
import { ConfigProvider as AConfigProvider } from '@arco-design/web-vue';
import { useAuthStore } from '@/store/authStore'; // 导入认证状态 store 的组合式函数，用于访问登录态能力。
import { useAppI18n } from '@/composables/useAppI18n';
import { useLegacyDomTranslation } from '@/composables/useLegacyDomTranslation';

const authStore = useAuthStore(); // 获取 authStore 实例，后续可调用认证状态检查方法。
const { arcoLocale } = useAppI18n();

useLegacyDomTranslation();

// 在应用启动时检查认证状态
onMounted(() => { // 在根组件挂载完成后执行一次初始化回调。
  authStore.checkAuthStatus(); // 触发认证状态校验（如读取本地 token 并同步登录状态）。
  console.log('App mounted, checking auth status'); // 输出调试日志，标记应用启动时已执行认证检查。
});
</script>

<style>
#app {
  font-family: "Sora", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-align: left;
  color: var(--theme-page-text);
  min-height: 100vh;
}

body, html {
  margin: 0;
  padding: 0;
  height: 100%;
  background: var(--theme-page-bg, #eef4f2);
}
</style>
