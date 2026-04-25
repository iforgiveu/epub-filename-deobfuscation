import os
import zipfile
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import re
import urllib.parse
import io


class EPUBFilenameCleaner:
    def __init__(self, input_dir=".", output_dir="./cleaned_epubs"):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)

        # XML命名空间
        self.ns = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'xhtml': 'http://www.w3.org/1999/xhtml',
            'svg': 'http://www.w3.org/2000/svg',
            'epub': 'http://www.idpf.org/2007/ops'
        }

    def find_epub_files(self):
        """递归查找所有EPUB文件"""
        epub_files = []
        for root, dirs, files in os.walk(self.input_dir):
            for file in files:
                if file.lower().endswith('.epub'):
                    full_path = Path(root) / file
                    epub_files.append(full_path)
        return epub_files

    def is_path_obfuscated(self, path_str):
        """检查路径是否被混淆"""
        try:
            decoded = urllib.parse.unquote(path_str)
            special_chars = sum(1 for c in decoded if c in '*?|:<>"\\/')
            has_url_encoded = any(x in path_str for x in ['%2A', '%3F', '%7C', '%3A', '%2F'])
            return special_chars > 2 or has_url_encoded
        except:
            return False

    def get_clean_filename_from_id(self, item_id, media_type=None):
        """从item的id生成规范文件名"""
        # 提取文件名（可能已经包含扩展名）
        id_path = Path(item_id)
        name = id_path.stem
        ext = id_path.suffix

        # 如果没有扩展名，根据media-type添加
        if not ext and media_type:
            ext_map = {
                'application/xhtml+xml': '.xhtml',
                'application/xml': '.xml',
                'text/css': '.css',
                'image/jpeg': '.jpg',
                'image/png': '.png',
                'image/gif': '.gif',
                'image/svg+xml': '.svg',
                'font/ttf': '.ttf',
                'font/otf': '.otf',
                'font/woff': '.woff',
                'font/woff2': '.woff2',
                'application/javascript': '.js',
                'text/javascript': '.js'
            }
            ext = ext_map.get(media_type, '')

        # 清理非法字符
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        name = name.strip('.')

        if not name:
            return None

        return f"{name}{ext}"


    def find_opf_file(self, content_dict):
        """在内存中找到OPF文件"""
        # 查找container.xml
        if "META-INF/container.xml" in content_dict:
            container_content = content_dict["META-INF/container.xml"]
            try:
                container_xml = ET.fromstring(container_content)
                for elem in container_xml.iter():
                    if 'rootfile' in elem.tag and 'full-path' in elem.attrib:
                        opf_path = elem.attrib['full-path']
                        directory = str(Path(opf_path).parent)
                        # breakpoint()
                        if opf_path in content_dict:
                            return opf_path, content_dict[opf_path]
            except:
                pass

        # 如果没有找到，查找所有.opf文件
        for file_path, content in content_dict.items():
            if file_path.endswith('.opf'):
                directory = str(Path(file_path).parent)
                return file_path, content

        return None, None

    def collect_rename_mapping(self, manifest, opf_dir):
        """收集需要重命名的文件映射 {旧路径: 新路径}"""
        rename_map = {}

        for item in manifest.findall('.//*[@href]'):
            href = item.get('href', '')
            item_id = item.get('id', '')
            media_type = item.get('media-type', '')

            # 检查是否需要修复
            if self.is_path_obfuscated(href):
                old_path = Path(href)

                # 使用id生成新文件名
                new_filename = self.get_clean_filename_from_id(item_id, media_type)

                if new_filename:
                    new_path = old_path.parent / new_filename

                    # 确保新路径和旧路径不同
                    if str(old_path) != str(new_path):
                        rename_map[str(old_path)] = str(new_path)
                        # print(f"    计划重命名: {old_path} -> {new_path} (基于id: {item_id})")

        return rename_map

    def rename_actual_files(self, content_dict, rename_map):
        """重命名内存中的实际文件"""
        renamed_files = []
        new_content_dict = {}

        # 首先复制所有文件
        for file_path, content in content_dict.items():
            # print(file_path,'\n')
            new_content_dict[file_path] = content
        # print(new_content_dict)
        # breakpoint()
        # 执行重命名
        for old_rel_path, new_rel_path in rename_map.items():
            old_rel_path_decoded = urllib.parse.unquote(old_rel_path)
            old_rel_path = Path(directory) / old_rel_path_decoded
            old_rel_path = old_rel_path.as_posix()
            # print(type(directory))
            new_rel_path = Path(directory) / new_rel_path
            new_rel_path = new_rel_path.as_posix()
            # print(old_rel_path_decoded)
            # breakpoint()
            # 在new_content_dict中查找并重命名
            if old_rel_path in new_content_dict:
                content = new_content_dict.pop(old_rel_path)
                new_content_dict[new_rel_path] = content
                renamed_files.append((old_rel_path, new_rel_path))
                print(f"    ✓ 已重命名文件: {old_rel_path} -> {new_rel_path}")
            else:
                print(f"    ✗ 文件不存在: {old_rel_path}")
                # 尝试查找相似文件（可能路径大小写问题）
                search_pattern = Path(old_rel_path).name
                found = [path for path in new_content_dict.keys() if path.endswith(search_pattern)]
                if found:
                    pass

        return renamed_files, new_content_dict

    def update_opf_references(self, opf_content, rename_map):
        """更新OPF文件中的href引用"""
        if not opf_content:
            return opf_content

        try:
            # 如果是bytes，先解码
            if isinstance(opf_content, bytes):
                content = opf_content.decode('utf-8')
                was_bytes = True
            else:
                content = opf_content
                was_bytes = False

            modified = False
            # 更新所有href引用
            for old_path, new_path in rename_map.items():
                old_path = Path(old_path).as_posix()
                new_path = Path(new_path).as_posix()
                # print(old_path,'::',new_path)
                if old_path in content:
                    content = content.replace(f'href="{old_path}"', f'href="{new_path}"')
                    content = content.replace(f"href='{old_path}'", f"href='{new_path}'")
                    modified = True
                    # print(f"    OPF更新引用: {old_path} -> {new_path}")

            # 如果原来是bytes，返回编码后的bytes
            if was_bytes:
                return content.encode('utf-8') if modified else opf_content
            else:
                return content if modified else opf_content

        except Exception as e:
            print(f"  更新OPF时出错: {str(e)}")
            return opf_content

    def update_file_references(self, content, rename_map, file_type="xhtml"):
        """更新文件中的所有引用"""
        try:
            # 如果是bytes，先解码
            if isinstance(content, bytes):
                content = content.decode('utf-8')
                was_bytes = True
            else:
                was_bytes = False

            modified = False

            # 匹配各种引用模式
            patterns = [
                (r'(src|href|poster|data)\s*=\s*"([^"]*)"', 2),
                (r"(src|href|poster|data)\s*=\s*'([^']*)'", 2),
                (r'@import\s+["\']([^"\']*)["\']', 1),
                (r'url\(["\']?([^"\'()]*)["\']?\)', 1),
            ]

            for pattern, group_idx in patterns:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    old_ref = match.group(group_idx)

                    # 检查这个引用是否需要更新
                    for old_path, new_path in rename_map.items():
                        old_filename = Path(old_path).name
                        new_filename = Path(new_path).name

                        # 如果引用中包含旧文件名
                        if old_filename in old_ref:
                            new_ref = old_ref.replace(old_filename, new_filename)
                            if new_ref != old_ref:
                                content = content.replace(match.group(0),
                                                          match.group(0).replace(old_ref, new_ref))
                                modified = True
                                # print(f"    {file_type.upper()}修复: {old_ref} -> {new_ref}")
                                break

            # 如果原来是bytes，返回编码后的bytes
            if was_bytes:
                return content.encode('utf-8')
            else:
                return content

        except Exception as e:
            # 某些文件可能不是文本文件，跳过
            return content

    def update_all_references(self, content_dict, rename_map):
        """更新所有文件中的引用"""
        updated_dict = {}

        for file_path, content in content_dict.items():
            file_ext = Path(file_path).suffix.lower()

            # 只处理文本文件
            if file_ext in ['.xhtml', '.html', '.htm', '.css', '.ncx', '.smil', '.xml', '.opf']:
                try:
                    if file_ext in ['.xhtml', '.html', '.htm']:
                        updated_dict[file_path] = self.update_file_references(content, rename_map, "xhtml")
                    elif file_ext == '.css':
                        updated_dict[file_path] = self.update_file_references(content, rename_map, "css")
                    elif file_ext == '.ncx':
                        updated_dict[file_path] = self.update_file_references(content, rename_map, "ncx")
                    elif file_ext == '.smil':
                        updated_dict[file_path] = self.update_file_references(content, rename_map, "smil")
                    elif file_ext == '.opf':
                        updated_dict[file_path] = self.update_opf_references(content, rename_map)
                    else:
                        updated_dict[file_path] = content
                except Exception as e:
                    print(f"    处理文件 {file_path} 时出错: {str(e)}")
                    updated_dict[file_path] = content
            else:
                updated_dict[file_path] = content

        return updated_dict

    def detect_files(self, content_dict):
        """自执行函数：检测第一个ttf文件和第一个webp文件的实际路径"""
        print("\n=== 文件检测结果 ===")

        # 查找第一个 ttf 文件
        ttf_files = [path for path in content_dict.keys() if path.lower().endswith('.ttf')]
        if ttf_files:
            first_ttf = ttf_files[0]
            print(f"第一个 TTF 文件: {first_ttf}")
            print(f"相对路径: {first_ttf}")
        else:
            print("未找到 TTF 文件")

        # 查找第一个 webp 文件
        webp_files = [path for path in content_dict.keys() if path.lower().endswith('.webp')]
        if webp_files:
            first_webp = webp_files[0]
            print(f"第一个 WEBP 文件: {first_webp}")
            print(f"相对路径: {first_webp}")
        else:
            print("未找到 WEBP 文件")

        print("=" * 40)

    def remove_zhangyue_expansion(self, epub_path):
        """处理单个EPUB文件"""
        try:
            relative_path = self.get_relative_path(epub_path)
            output_path = self.output_dir / relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # print(f"\n处理: {epub_path}")

            # 1. 读取EPUB到内存
            with open(epub_path, 'rb') as f:
                epub_data = f.read()

            # 2. 解压到内存字典
            content_dict = {}
            with zipfile.ZipFile(io.BytesIO(epub_data), 'r') as zf:
                for file_info in zf.filelist:
                    if not file_info.filename.endswith('/'):  # 跳过目录
                        try:
                            content_dict[file_info.filename] = zf.read(file_info.filename)
                        except:
                            pass

            # 调用文件检测函数
            self.detect_files(content_dict)

            # 3. 删除zhangyue-expansion.xml
            target_file = "META-INF/zhangyue-expansion.xml"
            target_file2 = "META-INF/encryption.xml"
            if target_file in content_dict:
                del content_dict[target_file]
            if target_file2 in content_dict:
                del content_dict[target_file2]
                # print("  已删除: META-INF/zhangyue-expansion.xml")

            # 4. 找到OPF文件
            opf_path, opf_content = self.find_opf_file(content_dict)
            if not opf_path:
                # 直接打包原文件
                self.repack_epub(content_dict, output_path)
                return True

            # 5. 解析OPF，获取manifest
            try:
                opf_dir = Path(opf_path).parent
                tree = ET.parse(io.BytesIO(opf_content))
                root = tree.getroot()

                # 查找manifest
                manifest = None
                for elem in root.iter():
                    if 'manifest' in elem.tag:
                        manifest = elem
                        break

                if manifest:
                    # 6. 收集需要重命名的文件映射
                    rename_map = self.collect_rename_mapping(manifest, opf_dir)

                    if rename_map:
                        # 7. 重命名内存中的实际文件
                        renamed_files, content_dict = self.rename_actual_files(content_dict, rename_map)

                        # 8-9. 更新所有文件中的引用（包括OPF）
                        content_dict = self.update_all_references(content_dict, rename_map)

            except ET.ParseError as e:
                print(f"  OPF解析错误: {str(e)}")
            except Exception as e:
                print(f"  处理OPF时出错: {str(e)}")

            # 10. 重新打包
            self.repack_epub(content_dict, output_path)

            return True

        except Exception as e:
            print(f"✗ 处理失败 {epub_path}: {str(e)}")
            return False

    def repack_epub(self, content_dict, output_path):
        """从内存重新打包EPUB"""
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path, content in content_dict.items():
                zf.writestr(file_path, content)

    def get_relative_path(self, file_path):
        """获取相对路径"""
        try:
            return file_path.relative_to(self.input_dir)
        except ValueError:
            return file_path.name

    def process_all(self):
        """处理所有EPUB文件"""
        epub_files = self.find_epub_files()

        if not epub_files:
            print("未找到EPUB文件")
            return

        print(f"找到 {len(epub_files)} 个EPUB文件")
        print("=" * 60)

        success_count = 0
        for i, epub_path in enumerate(epub_files, 1):
            print(f"\n[{i}/{len(epub_files)}] ", end="")
            if self.remove_zhangyue_expansion(epub_path):
                success_count += 1

        print(f"\n{'=' * 60}")
        print(f"处理完成: {success_count}/{len(epub_files)} 个文件成功")


def main():
    """主函数"""
    print("EPUB文件处理工具")
    print("=" * 60)
    print("功能:")
    print("  1. 删除 META-INF/zhangyue-expansion.xml和encryption.xml")
    print("  2. 修复混淆的文件名（基于item的id）")
    print("  3. 重命名实际文件")
    print("  4. 更新所有引用（OPF、XHTML、CSS、NCX等）")
    print("=" * 60)

    input_dir = input("输入目录 (默认为当前目录): ").strip() or "."
    output_dir = input("输出目录 (默认为 ./cleaned_epubs): ").strip() or "./cleaned_epubs"

    cleaner = EPUBFilenameCleaner(input_dir, output_dir)
    cleaner.process_all()


if __name__ == "__main__":
    directory = 'OEBPS'
    main()
