from config import *
from os import listdir
import os.path as os_path
import csv
'''for MNIST dataset: http://yann.lecun.com/exdb/mnist/'''
logger = logging.getLogger(__name__)

''' довавить вероятностную аугментацию и различия между тестовой и обучающей выборками, разделить препроцессинг и аугментацию'''
class DataLoader(object):
    def __init__(self, data_path, nrof_classes, dataset_type, shuffle, batch_size, transforms, transform_probs,
                 sample_type, labels_path=None, epoch_size=None, probabilities=None, balanced_undersampling=False):
        """
        :param data_path (string): путь до файла с данными.
        :param nrof_classes (int): количество классов в датасете.
        :param dataset_type (string): (['train', 'valid', 'test']).
        :param shuffle (bool): нужно ли перемешивать данные после очередной эпохи.
        :param batch_size (int): размер батча.
        :param transforms (list): список необходимых преобразований изображений.
        :param sample_type (string): (['default' - берем последовательно все данные, 'balanced' - сбалансированно,
        'prob' - сэмплирем с учетом указанных вероятностей])
        :param epoch_size (int or None): размер эпохи. Если None, необходимо посчитать размер эпохи (=размеру обучающей выюорки/batch_size)
        :param probabilities (array or None): в случае sample_type='prob' вероятности, с которыми будут выбраны элементы из каждого класса.
        """
        assert sample_type in ['default', 'balanced', 'prob']
        assert dataset_type in ['train', 'valid', 'test']
        assert len(transforms) == len(transform_probs)
        if probabilities is not None:
            assert len(probabilities) == nrof_classes
            assert sum(probabilities) == 1
            if isinstance(probabilities, list):
                probabilities = np.array(probabilities)

        #for MNIST only?
        self.sample_filenames = listdir(data_path)
        self.sample_filenames = set([i.split('.')[0] for i in self.sample_filenames])
        print(self.sample_filenames)\

        self.data_path = data_path


        self.number_of_samples = len(self.sample_filenames)
        self.batch_shape = (32, 32, 3)
        self.sample_size = self.batch_shape[0] * self.batch_shape[1] * self.batch_shape[2]
        logger.debug(f"samples count: {self.number_of_samples}, sample_shape: {self.batch_shape}, sample_size: {self.sample_size}")

        self.label_by_name = dict()
        self.labels = dict()
        self.nrof_classes = nrof_classes
        self.dataset_labels_list = np.array(range(self.nrof_classes))
        self.samples_by_label = [[] for _ in range(self.nrof_classes)]
        with open(labels_path) as file:
            reader = csv.reader(file)
            reader.__next__()
            k = 0
            for id, label in reader:
                if label not in self.label_by_name:
                    self.label_by_name[label] = k
                    k += 1
                self.labels[id] = self.label_by_name[label]
                if id in self.sample_filenames:
                    self.samples_by_label[self.label_by_name[label]].append(id)
        self.labels_count = k
        logger.debug(f"labels_count: {self.labels_count}, labels: {self.labels}")

        self.classes_size = [len(arr) for arr in self.samples_by_label]

        self.dataset_type = dataset_type
        self.shuffle = shuffle
        self.batch_size = batch_size

        if self.dataset_type == 'train':
            self.transforms = []
            self.transform_probs = []
            for index, transform in enumerate(transforms):
                self.transforms.append(transform)
                self.transform_probs.append(transform_probs[index] if not transform.is_mandatory() else 1)
        else:
            self.transforms = []
            self.transform_probs = []
            for index, transform in enumerate(transforms):
                if not transform.is_train_only():
                    self.transforms.append(transform)
                    self.transform_probs.append(transform_probs[index] if not transform.is_mandatory() else 1)

        self.sample_type = sample_type

        if self.sample_type == 'balanced':
            probabilities = np.ones(self.nrof_classes, dtype=np.float16) / self.nrof_classes
        self.probabilities = probabilities

        if epoch_size is None:
            if self.sample_type == 'default':
                epoch_size = self.number_of_samples // self.batch_size
            elif self.sample_type == 'prob':
                epoch_size = ((self.probabilities * self.classes_size).sum()).astype(np.int16) // self.batch_size
            else:
                if balanced_undersampling:
                    epoch_size = min(self.classes_size) * self.nrof_classes // self.batch_size
                else:
                    epoch_size = max(self.classes_size) * self.nrof_classes // self.batch_size

        self.epoch_size = epoch_size

        self.samples_cache = dict()
        self.current_batch = None
        self.shuffle_map = np.array([i for i in range(self.number_of_samples)])

    def read_data(self, id):
        '''
        :param sample_number: number of image in dataset
        :return: picture
        '''
        if id in self.samples_cache:
            return self.samples_cache[id]
        filename = os_path.join(self.data_path, '{}.png'.format(id))
        img = cv2.imread(filename)
        self.samples_cache[id] = img
        #print(f"{filename} was read. {img}")
        return img

    def show_statistics(self):
        """
        Необходимо вывести количество элементов в датасете, количество классов и количество элементов в каждом классе
        """
        print(f"Statistic:\n\tdataset_size: {self.number_of_samples}\n\tclass_count: {self.nrof_classes}\
                \n\tby_classes: {self.classes_size}\
                \n\tepoch_size: {self.epoch_size}")

    def one_hot_labels(self, labels):
        if isinstance(labels, (list, tuple, np.ndarray)):
            return [(self.dataset_labels_list == label).astype(np.float16) for label in labels]
        else:
            return (self.dataset_labels_list == labels).astype(np.float16)

    def batch_transform(self, batch):
        out_batch = []
        for image, label in batch:
            for index, transform in enumerate(self.transforms):
                if np.random.rand() < self.transform_probs[index]:
                    image = transform.call(image)
            out_batch.append((image, label))
        return out_batch

    def batch_generator(self):
        """
        Создание батчей на эпоху с учетом указанного размера эпохи и типа сэмплирования.
        """

        if self.sample_type == 'default':
            if self.shuffle:
                np.random.shuffle(self.shuffle_map)

            for epoch in range(self.epoch_size):
                batch = [(self.read_data(self.sample_filenames[self.shuffle_map[i]]), self.labels[self.sample_filenames[self.shuffle_map[i]]])
                         for i in range(epoch * self.batch_size, (epoch + 1) * self.batch_size)]
                batch = self.batch_transform(batch)
                self.current_batch = batch
                yield batch
        else:
            if self.shuffle:
                for arr in self.samples_by_label:
                    np.random.shuffle(arr)

            for epoch in range(self.epoch_size):
                class_choices = [np.random.choice(self.dataset_labels_list, p=self.probabilities)
                                 for _ in range(self.batch_size)]
                random_indexes = [np.random.choice(self.samples_by_label[_class]) for _class in class_choices]
                batch = [(self.read_data(index), self.labels[index]) for index in random_indexes]
                batch = self.batch_transform(batch)
                self.current_batch = batch
                yield batch

    def show_batch(self):
        """
        Необходимо вывести изображения в батче (один батч - одно окно). Предварительно привести значение в промежуток
        [0, 255) и типу к uint8
        :return:
        """
        batch = self.current_batch
        batch_shape = batch[0][0].shape
        res = np.zeros((batch_shape[0], batch_shape[1] * self.batch_size, batch_shape[2]), dtype=np.uint8)
        for index, img_label in enumerate(batch):
            img, label = img_label
            logger.debug(f"{index}: {label}, {self.one_hot_labels(label)}")
            res[:, index * batch_shape[1]:(index + 1) * batch_shape[1], :] = img[:,:,:]

        cv2.imshow(f"Batch size={self.batch_size}", res)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
