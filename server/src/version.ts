// 后台服务版本信息。协议版本统一从共用运行内核模块再导出，避免两份定义漂移。
export {
  API_VERSION,
  EVENT_CATALOG_VERSION,
  LEVEL_PROTOCOL_VERSION,
  PROJECT_FORMAT_VERSION,
  RUNTIME_KERNEL_VERSION,
} from '../../src/runtime/version.ts';

export const SERVICE_NAME = 'shiguang-studio-service';
export const SERVICE_VERSION = '0.1.1';
export const MCP_PROTOCOL_VERSIONS = ['2025-06-18', '2025-03-26', '2024-11-05'];
